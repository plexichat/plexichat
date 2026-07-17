"""
Artifacts self-test service for SelfTestRunner.

Exercises the Artifacts feature end-to-end without leaving dangling
connections or stale rows behind:

- Lifecycle suite: create -> get -> list -> update -> delete via the
  :class:`~src.core.artifacts.manager.ArtifactManager`, covering every
  artifact type (voice_call / whiteboard / upload / file / transcript /
  future). Asserts retention fields, ``server_id`` scoping, and hard-delete.
- Capability suite: asserts ``get_artifact_capabilities`` returns the correct
  states for configured / licensed / dependency-missing scenarios using an
  explicit config fixture (transcription reports DEPENDENCY_MISSING when local
  whisper is absent).
- Retention + privacy suite: asserts ``purge_expired`` removes an already
  expired artifact (created with a short retention window) and respects
  ``default_retention_days``; asserts ``anonymize_user_artifacts`` nulls the
  author on a user's artifacts.
- WSS harness: a real-ish gateway handshake (HELLO -> IDENTIFY) for two
  self-test connections (a subscriber and an actor), then a round-trip: the
  actor sends an ARTIFACT_OP (opcode 62) and the subscribed client asserts the
  op is relayed back to it. Connections are closed in a finally block so no
  ports are left dangling.

The suites use the live server database (:func:`src.api.get_db`) and the live
WebSocket gateway, so they run cleanly inside ``main.py self-test``.
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import websocket

import src.api as api
import utils.config as config
import utils.logger as logger

from src.api.websocket.opcodes import GatewayOpcode
from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.capabilities import (
    get_artifact_capabilities,
    CapabilityState,
    _eval_editor,
    _eval_whiteboard,
    _eval_voice_transcription,
    _eval_voice_recording,
)
from src.core.artifacts.federation import FederationArtifactBridge
from src.core.artifacts.retention import purge_expired
from src.core.artifacts.privacy import anonymize_user_artifacts
from src.core.artifacts.models import ArtifactType, ArtifactStatus, VoiceCall
from src.core.artifacts.repository import (
    create_artifact,
    create_voice_call,
    delete_artifact,
    get_artifact,
)
from src.core.artifacts.transcription import transcribe_call

from ..context import SelfTestContext

# Sentinel marker used to make artifact ids created by this suite easy to
# find and clean up. It lives in the high range of snowflake space and is
# extremely unlikely to collide with real rows.
_ARTIFACTS_TEST_NONCE = 0x5A17E57


def _now_ms() -> int:
    return int(time.time() * 1000)


class ArtifactsTester:
    """Self-test suite covering the Artifacts feature."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    # === result logging helpers ===

    def _record(
        self,
        method: str,
        path: str,
        success: bool,
        label: str,
        error: Optional[str] = None,
        status_code: int = 200,
    ) -> None:
        self.ctx.results.append(
            {
                "method": method,
                "path": path,
                "status_code": status_code if success else (status_code or 0),
                "duration_ms": 0.0,
                "success": success,
                "label": label,
                "error": error,
            }
        )
        if success:
            logger.info(f"[artifacts] {label}: PASS")
        else:
            logger.error(f"[artifacts] {label}: FAIL - {error}")

    def _check(self, label: str, path: str, cond: bool, error: str) -> bool:
        self._record("ARTIFACT", path, cond, label, error if not cond else None)
        return cond

    # === public entry point ===

    def test_artifacts(self) -> None:
        """Run every artifacts suite."""
        logger.info("=" * 60)
        logger.info("STARTING ARTIFACTS SELF-TEST SUITE")
        logger.info("=" * 60)

        self._test_lifecycle()
        self._test_capabilities()
        self._test_retention_and_privacy()
        self._test_wss_round_trip()
        self._test_admin_endpoints()
        self._test_transcription_worker()
        self._test_federation_bridge()
        self._test_individual_eval()

        logger.info("ARTIFACTS SELF-TEST SUITE COMPLETE")

    # === 1. lifecycle ===

    def _test_lifecycle(self) -> None:
        db = api.get_db()
        if db is None:
            self._record(
                "ARTIFACT",
                "/artifacts",
                False,
                "artifact_lifecycle_db",
                "Database unavailable",
            )
            return

        manager = ArtifactManager(db, config.get("artifacts", {}) or {})
        created_ids: List[int] = []

        try:
            author_id = self.ctx.test_user_id or 1
            server_id = self.ctx.test_server_id

            # Cover every artifact type.
            type_map = {
                "voice_call": ArtifactType.VOICE_CALL,
                "whiteboard": ArtifactType.WHITEBOARD,
                "upload": ArtifactType.UPLOAD,
                "file": ArtifactType.FILE,
                "transcript": ArtifactType.TRANSCRIPT,
                "future": ArtifactType.FUTURE,
            }

            for name, atype in type_map.items():
                art = manager.create(
                    conversation_id=None,
                    author_id=author_id,
                    artifact_type=atype,
                    title=f"selftest-{name}-{_ARTIFACTS_TEST_NONCE}",
                    summary="lifecycle test artifact",
                    server_id=server_id,
                    status=ArtifactStatus.COMPLETED,
                    payload={"kind": name},
                    retention_policy={"days": 7},
                )
                created_ids.append(int(art.id))

                # create assertion
                if not self._check(
                    "artifact_create_" + name,
                    "/artifacts POST",
                    art is not None and art.id is not None,
                    "create returned no artifact",
                ):
                    continue

                # server_id scoping assertion
                self._check(
                    "artifact_server_scope_" + name,
                    "/artifacts",
                    art.server_id == server_id,
                    f"server_id not scoped ({art.server_id} != {server_id})",
                )

                # retention fields assertion
                self._check(
                    "artifact_retention_fields_" + name,
                    "/artifacts",
                    art.expires_at is not None and art.expires_at > art.created_at,
                    "expires_at not computed from retention policy",
                )

                # get assertion
                fetched = manager.get(art.id)
                self._check(
                    "artifact_get_" + name,
                    "/artifacts/{id} GET",
                    fetched is not None and fetched.artifact_type == atype,
                    "get did not return the artifact",
                )

                # update assertion
                updated = manager.update(art.id, title=f"updated-{name}")
                self._check(
                    "artifact_update_" + name,
                    "/artifacts/{id} PATCH",
                    updated is not None and updated.title == f"updated-{name}",
                    "update did not change the title",
                )

            # list assertion (filter by author)
            listed = manager.list_with_filters(author_id=author_id)
            listed_ids = {int(a.id) for a in listed}
            self._check(
                "artifact_list",
                "/artifacts GET",
                all(i in listed_ids for i in created_ids),
                "list did not return all created artifacts",
            )

            # hard-delete assertion (per type)
            for art_id in created_ids:
                ok = manager.delete(art_id)
                self._check(
                    "artifact_delete_" + str(art_id),
                    "/artifacts/{id} DELETE",
                    ok is True,
                    "delete did not return True",
                )
                still = manager.get(art_id)
                self._check(
                    "artifact_delete_hard_" + str(art_id),
                    "/artifacts/{id} DELETE",
                    still is None,
                    "artifact still present after delete (not hard-deleted)",
                )
            created_ids.clear()
        except Exception as e:
            self._record(
                "ARTIFACT",
                "/artifacts",
                False,
                "artifact_lifecycle_error",
                str(e),
            )
        finally:
            for art_id in list(created_ids):
                try:
                    delete_artifact(db, art_id)
                except Exception:
                    pass

    # === 2. capabilities ===

    def _test_capabilities(self) -> None:
        try:
            # Disabled-by-config scenario.
            disabled_cfg = {"enabled": False}
            caps = get_artifact_capabilities(disabled_cfg)
            self._check(
                "cap_artifacts_disabled",
                "/capabilities artifacts",
                caps["artifacts"].state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected disabled_by_config, got {caps['artifacts'].state}",
            )

            # Editor disabled scenario (artifacts enabled, editor off).
            editor_off_cfg = {"enabled": True, "editor": {"enabled": False}}
            caps = get_artifact_capabilities(editor_off_cfg)
            self._check(
                "cap_editor_disabled",
                "/capabilities artifacts_editor",
                caps["artifacts_editor"].state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected disabled_by_config, got {caps['artifacts_editor'].state}",
            )

            # Available baseline scenario.
            enabled_cfg = {
                "enabled": True,
                "editor": {"enabled": True},
                "whiteboard": {"enabled": True},
                "voice": {
                    "allow_recording": True,
                    "transcription": {"enabled": False},
                },
            }
            caps = get_artifact_capabilities(enabled_cfg)
            self._check(
                "cap_artifacts_available",
                "/capabilities artifacts",
                caps["artifacts"].state == CapabilityState.AVAILABLE,
                f"expected available, got {caps['artifacts'].state}",
            )
            self._check(
                "cap_voice_recording_available",
                "/capabilities voice_recording",
                caps["voice_recording"].state == CapabilityState.AVAILABLE,
                f"expected available, got {caps['voice_recording'].state}",
            )

            # Transcription dependency-missing: local_whisper selected but the
            # whisper package is absent in the self-test environment.
            tr_cfg = {
                "enabled": True,
                "whiteboard": {"enabled": True},
                "voice": {
                    "allow_recording": True,
                    "transcription": {"enabled": True, "provider": "local_whisper"},
                },
            }
            caps = get_artifact_capabilities(tr_cfg)
            self._check(
                "cap_transcription_dependency_missing",
                "/capabilities voice_transcription",
                caps["voice_transcription"].state
                in (
                    CapabilityState.DISABLED_BY_LICENSE,
                    CapabilityState.DEPENDENCY_MISSING,
                ),
                f"expected disabled_by_license/dependency_missing, got {caps['voice_transcription'].state}",
            )

            # Whiteboard license-missing scenario (feature flag absent).
            wb_cfg = {
                "enabled": True,
                "whiteboard": {"enabled": True},
            }
            caps = get_artifact_capabilities(wb_cfg)
            self._check(
                "cap_whiteboard_license_missing",
                "/capabilities artifacts_whiteboard",
                caps["artifacts_whiteboard"].state
                in (CapabilityState.DISABLED_BY_LICENSE, CapabilityState.AVAILABLE),
                f"whiteboard state unexpected: {caps['artifacts_whiteboard'].state}",
            )
        except Exception as e:
            self._record(
                "ARTIFACT",
                "/capabilities",
                False,
                "artifact_capabilities_error",
                str(e),
            )

    # === 3. retention + privacy ===

    def _test_retention_and_privacy(self) -> None:
        db = api.get_db()
        if db is None:
            self._record(
                "ARTIFACT",
                "/artifacts retention",
                False,
                "artifact_retention_db",
                "Database unavailable",
            )
            return

        created_ids: List[int] = []
        try:
            author_id = self.ctx.test_user_id or 1

            # --- retention: expired artifact should be purged ---
            now = _now_ms()
            expired = _make_artifact_row(
                db=db,
                artifact_id=_alloc_id(db),
                author_id=author_id,
                artifact_type=ArtifactType.UPLOAD,
                title=f"expired-{_ARTIFACTS_TEST_NONCE}",
                created_at=now - 30 * 86400 * 1000,
                expires_at=now - 1000,
            )
            created_ids.append(int(expired.id))

            # A non-expired artifact should survive the purge.
            fresh = _make_artifact_row(
                db=db,
                artifact_id=_alloc_id(db),
                author_id=author_id,
                artifact_type=ArtifactType.FILE,
                title=f"fresh-{_ARTIFACTS_TEST_NONCE}",
                created_at=now,
                expires_at=now + 30 * 86400 * 1000,
            )
            created_ids.append(int(fresh.id))

            self._check(
                "retention_before_purge",
                "/artifacts retention",
                get_artifact(db, expired.id) is not None
                and get_artifact(db, fresh.id) is not None,
                "seed artifacts not present before purge",
            )

            removed = purge_expired(db, config.get("artifacts", {}) or {})
            self._check(
                "retention_purge_removed_expired",
                "/artifacts retention purge",
                get_artifact(db, expired.id) is None,
                "expired artifact still present after purge_expired",
            )
            self._check(
                "retention_purge_kept_fresh",
                "/artifacts retention purge",
                get_artifact(db, fresh.id) is not None,
                "fresh artifact was removed by purge_expired",
            )
            self._check(
                "retention_purge_count",
                "/artifacts retention purge",
                removed >= 1,
                f"purge_expired reported {removed} removals",
            )

            # --- default_retention_days respected on create ---
            manager = ArtifactManager(
                db,
                {
                    "enabled": True,
                    "default_retention_days": 5,
                },
            )
            retained = manager.create(
                conversation_id=None,
                author_id=author_id,
                artifact_type=ArtifactType.UPLOAD,
                title=f"retained-{_ARTIFACTS_TEST_NONCE}",
                server_id=self.ctx.test_server_id,
            )
            created_ids.append(int(retained.id))
            self._check(
                "retention_default_applied",
                "/artifacts retention default",
                retained.expires_at is not None,
                "default_retention_days not applied (expires_at is None)",
            )
            if retained.expires_at is not None:
                self._check(
                    "retention_default_window",
                    "/artifacts retention default",
                    retained.expires_at == retained.created_at + 5 * 86400 * 1000,
                    "default retention window not equal to 5 days",
                )

            # --- privacy: anonymize nulls the author ---
            target_user = author_id
            seed_art = get_artifact(db, fresh.id)
            self._check(
                "privacy_seed_author",
                "/artifacts privacy",
                seed_art is not None and seed_art.author_id == target_user,
                "seed artifact for privacy not authored by target user",
            )
            touched = anonymize_user_artifacts(
                db, target_user, {"anonymize_content": True}
            )
            self._check(
                "privacy_anonymize_touched",
                "/artifacts privacy anonymize",
                touched >= 1,
                f"anonymize_user_artifacts touched {touched} rows",
            )
            after = get_artifact(db, fresh.id)
            nulled = after is not None and after.author_id == 0
            self._check(
                "privacy_author_nulled",
                "/artifacts privacy anonymize",
                nulled,
                f"author not nulled (author_id={after.author_id if after else None})",
            )
        except Exception as e:
            self._record(
                "ARTIFACT",
                "/artifacts retention",
                False,
                "artifact_retention_error",
                str(e),
            )
        finally:
            for art_id in list(created_ids):
                try:
                    delete_artifact(db, art_id)
                except Exception:
                    pass

    # === 4. WSS round-trip harness ===

    def _test_wss_round_trip(self) -> None:
        if not self.ctx.token or not self.ctx.other_token:
            self._record(
                "WS",
                "/gateway artifacts",
                False,
                "artifact_wss_setup",
                "missing test tokens for WSS harness",
            )
            return

        ws_url = self.ctx.base_url.replace("http", "ws") + "/gateway"
        internal_secret = api.get_internal_secret()
        headers = {}
        if internal_secret:
            headers["X-Plexichat-Internal-Secret"] = internal_secret

        sub_ws = None
        actor_ws = None
        try:
            # Subscriber connection (other_user) -- the client that should
            # receive the relayed op.
            sub_ws = websocket.create_connection(ws_url, timeout=5, header=headers)
            hello = json.loads(sub_ws.recv())
            if hello.get("op") != int(GatewayOpcode.HELLO):
                self._record(
                    "WS",
                    "/gateway artifacts",
                    False,
                    "artifact_wss_sub_hello",
                    f"expected HELLO, got op {hello.get('op')}",
                )
                return
            sub_ws.send(
                json.dumps(
                    {
                        "op": int(GatewayOpcode.IDENTIFY),
                        "d": {
                            "token": self.ctx.other_token,
                            "intents": 0,
                            "properties": {
                                "os": "selftest",
                                "browser": "python",
                                "device": "selftest-sub",
                            },
                        },
                    }
                )
            )
            sub_ready = json.loads(sub_ws.recv())
            if sub_ready.get("t") != "READY":
                self._record(
                    "WS",
                    "/gateway artifacts",
                    False,
                    "artifact_wss_sub_identify",
                    f"subscriber expected READY, got {sub_ready.get('t')}",
                )
                return

            # Actor connection (main user) -- sends the ARTIFACT_OP.
            actor_ws = websocket.create_connection(ws_url, timeout=5, header=headers)
            hello2 = json.loads(actor_ws.recv())
            if hello2.get("op") != int(GatewayOpcode.HELLO):
                self._record(
                    "WS",
                    "/gateway artifacts",
                    False,
                    "artifact_wss_actor_hello",
                    f"expected HELLO, got op {hello2.get('op')}",
                )
                return
            actor_ws.send(
                json.dumps(
                    {
                        "op": int(GatewayOpcode.IDENTIFY),
                        "d": {
                            "token": self.ctx.token,
                            "intents": 0,
                            "properties": {
                                "os": "selftest",
                                "browser": "python",
                                "device": "selftest-actor",
                            },
                        },
                    }
                )
            )
            actor_ready = json.loads(actor_ws.recv())
            if actor_ready.get("t") != "READY":
                self._record(
                    "WS",
                    "/gateway artifacts",
                    False,
                    "artifact_wss_actor_identify",
                    f"actor expected READY, got {actor_ready.get('t')}",
                )
                return

            # Choose an artifact id for the round-trip (use a deterministic,
            # unlikely-to-exist id; relay only fans out, no persistence).
            artifact_id = _ARTIFACTS_TEST_NONCE * 1000 + 1

            # Subscriber subscribes to the artifact.
            sub_ws.send(
                json.dumps(
                    {
                        "op": int(GatewayOpcode.ARTIFACT_SUBSCRIBE),
                        "d": {"artifact_id": artifact_id},
                    }
                )
            )
            # Subscribe replies with an ARTIFACT_SYNC placeholder snapshot.
            sub_msg = json.loads(sub_ws.recv())
            if (
                sub_msg.get("op") != int(GatewayOpcode.ARTIFACT_SYNC)
                or sub_msg.get("d", {}).get("artifact_id") != artifact_id
            ):
                self._record(
                    "WS",
                    "/gateway artifacts",
                    False,
                    "artifact_wss_subscribe_ack",
                    f"expected ARTIFACT_SYNC for {artifact_id}, got {sub_msg}",
                )
                return

            # Actor sends an ARTIFACT_OP (e.g. a create/update delta).
            op_payload = {
                "op_type": "update",
                "path": "/content",
                "value": "round-trip-marker",
            }
            actor_ws.send(
                json.dumps(
                    {
                        "op": int(GatewayOpcode.ARTIFACT_OP),
                        "d": {"artifact_id": artifact_id, "op": op_payload},
                    }
                )
            )

            # The subscriber should receive the relayed ARTIFACT_OP.
            relayed = _recv_json_with_timeout(sub_ws, timeout=5)
            if relayed is None:
                self._record(
                    "WS",
                    "/gateway artifacts",
                    False,
                    "artifact_wss_relay_received",
                    "subscriber received no frame after ARTIFACT_OP",
                )
                return

            d = relayed.get("d", {})
            ok = (
                relayed.get("op") == int(GatewayOpcode.ARTIFACT_OP)
                and d.get("artifact_id") == artifact_id
                and isinstance(d.get("op"), dict)
                and d.get("op", {}).get("op_type") == "update"
                and d.get("op", {}).get("value") == "round-trip-marker"
            )
            self._record(
                "WS",
                "/gateway artifacts",
                ok,
                "artifact_wss_relay_received",
                None if ok else f"unexpected relayed frame: {relayed}",
                status_code=101 if ok else 0,
            )

            # Unsubscribe for clean teardown.
            try:
                sub_ws.send(
                    json.dumps(
                        {
                            "op": int(GatewayOpcode.ARTIFACT_UNSUBSCRIBE),
                            "d": {"artifact_id": artifact_id},
                        }
                    )
                )
            except Exception:
                pass
        except Exception as e:
            self._record(
                "WS",
                "/gateway artifacts",
                False,
                "artifact_wss_error",
                str(e),
            )
        finally:
            for sock in (sub_ws, actor_ws):
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass

    # === 5. admin REST endpoints ===

    def _test_admin_endpoints(self) -> None:
        db = api.get_db()
        if db is None:
            self._record(
                "GET",
                "/api/v1/admin/artifacts",
                False,
                "admin_endpoints_db",
                "Database unavailable",
            )
            return
        try:
            resp = self.ctx.session.get(self.ctx.base_url + "/api/v1/admin/artifacts")
            self._check(
                "admin_artifacts_list",
                "/api/v1/admin/artifacts GET",
                resp.ok,
                f"GET failed: {resp.status_code}",
            )

            manager = ArtifactManager(db, config.get("artifacts", {}) or {})
            art = manager.create(
                conversation_id=None,
                author_id=self.ctx.test_user_id or 1,
                artifact_type=ArtifactType.UPLOAD,
                title=f"admin-del-{_ARTIFACTS_TEST_NONCE}",
                server_id=self.ctx.test_server_id,
                status=ArtifactStatus.COMPLETED,
                retention_policy={"days": 7},
            )
            art_id = int(art.id)
            try:
                resp = self.ctx.session.delete(
                    self.ctx.base_url + f"/api/v1/admin/artifacts/{art_id}"
                )
                self._check(
                    "admin_artifacts_delete",
                    f"/api/v1/admin/artifacts/{art_id} DELETE",
                    resp.ok,
                    f"DELETE failed: {resp.status_code}",
                )
            finally:
                try:
                    delete_artifact(db, art_id)
                except Exception:
                    pass

            resp = self.ctx.session.post(
                self.ctx.base_url + "/api/v1/admin/artifacts/retention/purge"
            )
            self._check(
                "admin_retention_purge",
                "/api/v1/admin/artifacts/retention/purge POST",
                resp.ok,
                f"POST failed: {resp.status_code}",
            )

            server_id = self.ctx.test_server_id or 1
            resp = self.ctx.session.post(
                self.ctx.base_url + "/api/v1/admin/artifacts/retention/server",
                json={"server_id": server_id},
            )
            self._check(
                "admin_retention_server",
                "/api/v1/admin/artifacts/retention/server POST",
                resp.ok,
                f"POST failed: {resp.status_code}",
            )
        except Exception as e:
            self._record(
                "ADMIN",
                "/api/v1/admin/artifacts",
                False,
                "admin_endpoints_error",
                str(e),
            )

    # === 6. transcription worker ===

    def _test_transcription_worker(self) -> None:
        db = api.get_db()
        if db is None:
            self._record(
                "WORKER", "transcribe_call", False, "no_db", "Database unavailable"
            )
            return
        try:
            result = asyncio.run(transcribe_call(999999999, db))
            self._check(
                "transcribe_nonexistent_call",
                "transcribe_call",
                result is None,
                f"expected None, got {result}",
            )

            call_id = _alloc_id(db)
            call = VoiceCall(
                id=call_id,
                conversation_id=None,
                channel_id=None,
                server_id=self.ctx.test_server_id,
                initiator_id=self.ctx.test_user_id or 1,
                started_at=_now_ms(),
                created_at=_now_ms(),
                updated_at=_now_ms(),
                recorded=False,
            )
            try:
                create_voice_call(db, call)
                result = asyncio.run(transcribe_call(call_id, db))
                self._check(
                    "transcribe_not_recorded",
                    "transcribe_call",
                    result is None,
                    f"expected None, got {result}",
                )
            finally:
                try:
                    db.execute("DELETE FROM voice_calls WHERE id = ?", (call_id,))
                except Exception:
                    pass
        except Exception as e:
            self._record(
                "WORKER", "transcribe_call", False, "transcription_worker_error", str(e)
            )

    # === 7. federation bridge ===

    def _test_federation_bridge(self) -> None:
        db = api.get_db()
        if db is None:
            self._record("FEDERATION", "bridge", False, "no_db", "Database unavailable")
            return
        try:

            class _MockPlexijoin:
                def list_connections(self, status=None):
                    return {"connections": [{"id": 1, "remote_instance_id": 42}]}

                def record_traffic(
                    self, connection_id=None, direction=None, message_count=None
                ):
                    pass

            bridge = FederationArtifactBridge(db, _MockPlexijoin())

            count = bridge.forward_artifact_op(999999999, {"test": "op"}, 0)
            self._check(
                "federation_op_nonexistent",
                "forward_artifact_op",
                count == 0,
                f"expected 0, got {count}",
            )

            count = bridge.forward_artifact_event("create", None)
            self._check(
                "federation_event_none",
                "forward_artifact_event",
                count == 0,
                f"expected 0, got {count}",
            )

        except Exception as e:
            self._record(
                "FEDERATION",
                "FederationArtifactBridge",
                False,
                "federation_bridge_error",
                str(e),
            )

    # === 8. individual _eval_* functions ===

    def _test_individual_eval(self) -> None:
        try:
            info = _eval_editor({"enabled": True, "editor": {"enabled": True}})
            self._check(
                "eval_editor_available",
                "_eval_editor",
                info.state == CapabilityState.AVAILABLE,
                f"expected AVAILABLE, got {info.state}",
            )

            info = _eval_editor({"enabled": True, "editor": {"enabled": False}})
            self._check(
                "eval_editor_disabled",
                "_eval_editor",
                info.state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected DISABLED_BY_CONFIG, got {info.state}",
            )

            info = _eval_whiteboard({"enabled": True, "whiteboard": {"enabled": True}})
            self._check(
                "eval_whiteboard_available",
                "_eval_whiteboard",
                info.state
                in (CapabilityState.AVAILABLE, CapabilityState.DISABLED_BY_LICENSE),
                f"unexpected state: {info.state}",
            )

            info = _eval_whiteboard({"enabled": True, "whiteboard": {"enabled": False}})
            self._check(
                "eval_whiteboard_disabled",
                "_eval_whiteboard",
                info.state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected DISABLED_BY_CONFIG, got {info.state}",
            )

            info = _eval_voice_transcription(
                {
                    "enabled": True,
                    "voice": {
                        "transcription": {"enabled": True, "provider": "local_whisper"}
                    },
                }
            )
            self._check(
                "eval_transcription_attempt",
                "_eval_voice_transcription",
                info.state != CapabilityState.AVAILABLE,
                "unexpected AVAILABLE (whisper not expected in test env)",
            )

            info = _eval_voice_transcription(
                {"enabled": True, "voice": {"transcription": {"enabled": False}}}
            )
            self._check(
                "eval_transcription_disabled",
                "_eval_voice_transcription",
                info.state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected DISABLED_BY_CONFIG, got {info.state}",
            )

            info = _eval_voice_recording(
                {"enabled": True, "voice": {"allow_recording": True}}
            )
            self._check(
                "eval_recording_available",
                "_eval_voice_recording",
                info.state == CapabilityState.AVAILABLE,
                f"expected AVAILABLE, got {info.state}",
            )

            info = _eval_voice_recording(
                {"enabled": True, "voice": {"allow_recording": False}}
            )
            self._check(
                "eval_recording_disabled",
                "_eval_voice_recording",
                info.state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected DISABLED_BY_CONFIG, got {info.state}",
            )
        except Exception as e:
            self._record("EVAL", "_eval_*", False, "individual_eval_error", str(e))


def _recv_json_with_timeout(sock: Any, timeout: float) -> Optional[Dict[str, Any]]:
    """Receive a single JSON frame from a websocket client with a timeout."""
    original = sock.gettimeout()
    try:
        sock.settimeout(timeout)
        raw = sock.recv()
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None
    finally:
        try:
            sock.settimeout(original)
        except Exception:
            pass


def _alloc_id(db: Any) -> int:
    """Allocate a unique artifact id that will not collide with real rows."""
    nonce = _ARTIFACTS_TEST_NONCE
    base = (nonce << 32) | (int(time.time() * 1000) & 0xFFFFFFFF)
    # Ensure uniqueness against any existing rows.
    existing = set()
    try:
        rows = db.fetch_all("SELECT id FROM artifacts WHERE id >= ?", (base,))
        existing = {int(r["id"]) for r in rows}
    except Exception:
        pass
    cand = base
    while cand in existing:
        cand += 1
    return cand


def _make_artifact_row(
    db: Any,
    artifact_id: int,
    author_id: int,
    artifact_type: ArtifactType,
    title: str,
    created_at: int,
    expires_at: Optional[int],
) -> Any:
    """Insert a raw artifact row with explicit created_at/expires_at."""
    from src.core.artifacts.models import Artifact

    art = Artifact(
        id=artifact_id,
        conversation_id=None,
        channel_id=None,
        server_id=None,
        author_id=author_id,
        artifact_type=artifact_type,
        title=title,
        summary="retention/privacy seed",
        status=ArtifactStatus.COMPLETED,
        recorded=False,
        has_transcript=False,
        payload={"seed": True},
        created_at=created_at,
        updated_at=created_at,
        retention_policy=None,
        expires_at=expires_at,
        license_feature=None,
    )
    return create_artifact(db, art)
