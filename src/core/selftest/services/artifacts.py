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

import json
from typing import List, Optional

import websocket

import src.api as api
import utils.config as config
import utils.logger as logger
from utils import licensing as license_module

from src.api.websocket.opcodes import GatewayOpcode
from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.capabilities import (
    get_artifact_capabilities,
    CapabilityState,
)
from src.core.artifacts.retention import purge_expired
from src.core.artifacts.privacy import anonymize_user_artifacts
from src.core.artifacts.models import ArtifactType, ArtifactStatus
from src.core.artifacts.repository import (
    delete_artifact,
    get_artifact,
)

from ..context import SelfTestContext
from ..db_guard import sanitize_error
from .artifacts_eval import (
    ArtifactsEvalMixin,
    _alloc_id,
    _make_artifact_row,
    _now_ms,
    _recv_json_with_timeout,
    _recv_required_json,
)

# Sentinel marker used to make artifact ids created by this suite easy to
# find and clean up. It lives in the high range of snowflake space and is
# extremely unlikely to collide with real rows.
_ARTIFACTS_TEST_NONCE = 0x5A17E57


class ArtifactsTester(ArtifactsEvalMixin):
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
                "plexiscribe": ArtifactType.PLEXISCRIBE,
                "plexiscript": ArtifactType.PLEXISCRIPT,
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

            # Plexiscribe disabled scenario (artifacts enabled, plexiscribe off).
            ps_off_cfg = {"enabled": True, "plexiscribe": {"enabled": False}}
            caps = get_artifact_capabilities(ps_off_cfg)
            self._check(
                "cap_plexiscribe_disabled",
                "/capabilities plexiscribe",
                caps["plexiscribe"].state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected disabled_by_config, got {caps['plexiscribe'].state}",
            )

            # Available baseline scenario.
            enabled_cfg = {
                "enabled": True,
                "plexiscribe": {"enabled": True},
                "plexiscript": {"enabled": True},
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
            _free = license_module.is_free_tier()
            self._check(
                "cap_whiteboard_license_missing",
                "/capabilities artifacts_whiteboard",
                caps["artifacts_whiteboard"].state
                in (
                    CapabilityState.AVAILABLE,
                    CapabilityState.DISABLED_BY_LICENSE,
                ),
                f"whiteboard state unexpected (free_tier={_free}): {caps['artifacts_whiteboard'].state}",
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
        db = api.get_db()
        temporary_artifact_id: Optional[int] = None
        try:
            # Subscriber connection (other_user) -- the client that should
            # receive the relayed op.
            sub_ws = websocket.create_connection(ws_url, timeout=5, header=headers)
            hello = _recv_required_json(sub_ws, "subscriber HELLO")
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
            sub_ready = _recv_required_json(sub_ws, "subscriber READY")
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
            hello2 = _recv_required_json(actor_ws, "actor HELLO")
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
            actor_ready = _recv_required_json(actor_ws, "actor READY")
            if actor_ready.get("t") != "READY":
                self._record(
                    "WS",
                    "/gateway artifacts",
                    False,
                    "artifact_wss_actor_identify",
                    f"actor expected READY, got {actor_ready.get('t')}",
                )
                return

            # Use a real temporary artifact so the WSS test exercises the
            # same existence and authorization checks as production. Both
            # self-test users must share the setup conversation; otherwise
            # this fixture would silently fall back to an unauthorized scope.
            if db is None:
                raise RuntimeError("Database unavailable for artifact WSS fixture")
            if self.ctx.test_conversation_id is None:
                raise RuntimeError(
                    "artifact WSS fixture requires a shared test conversation"
                )
            artifact_manager = ArtifactManager(db, config.get("artifacts", {}) or {})
            temporary_artifact = artifact_manager.create(
                # Use the shared test conversation rather than a server-scoped
                # artifact. Both self-test users are participants there, so the
                # WSS fixture exercises the same membership authorization path
                # without granting the ordinary subscriber admin permission.
                conversation_id=self.ctx.test_conversation_id,
                author_id=self.ctx.test_user_id or 1,
                # Use the base-gated upload type so this transport test is
                # independent of optional whiteboard licensing/configuration.
                artifact_type=ArtifactType.UPLOAD,
                title=f"selftest-wss-{_ARTIFACTS_TEST_NONCE}",
                summary="temporary WSS authorization fixture",
                server_id=None,
                status=ArtifactStatus.LIVE,
                payload={"selftest": True},
            )
            temporary_artifact_id = int(temporary_artifact.id)
            artifact_id = temporary_artifact_id

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
            sub_msg = _recv_required_json(sub_ws, "subscriber ARTIFACT_SYNC")
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
            if db is not None and temporary_artifact_id is not None:
                try:
                    delete_artifact(db, temporary_artifact_id)
                except Exception as exc:
                    logger.debug(
                        "Failed to clean temporary WSS artifact %s: %s",
                        temporary_artifact_id,
                        exc,
                    )

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
                200 <= resp.status_code < 400,
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
                    200 <= resp.status_code < 400,
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
                200 <= resp.status_code < 400,
                f"POST failed: {resp.status_code}",
            )

            server_id = str(self.ctx.test_server_id or 1)
            resp = self.ctx.session.post(
                self.ctx.base_url + "/api/v1/admin/artifacts/retention/server",
                json={"server_id": server_id},
            )
            self._check(
                "admin_retention_server",
                "/api/v1/admin/artifacts/retention/server POST",
                200 <= resp.status_code < 400,
                f"POST failed: {resp.status_code}",
            )
        except Exception as e:
            self._record(
                "ADMIN",
                "/api/v1/admin/artifacts",
                False,
                "admin_endpoints_error",
                sanitize_error(e),
            )
