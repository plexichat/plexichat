"""Worker / federation / eval suites for the artifacts self-test service.

Split out of ``artifacts.py`` (which previously exceeded 1000 lines)
while keeping the public import surface stable: ``ArtifactsTester``
composes this mixin, and the module-level helpers are re-exported from
``artifacts.py`` for internal use.
"""

import asyncio
import json
import secrets
import time
from typing import Any, Dict, Optional

import src.api as api

from ..db_guard import sanitize_error
from src.core.artifacts.capabilities import (
    CapabilityState,
    _eval_plexiboard,
    _eval_plexiscribe,
    _eval_plexiscript,
    _eval_voice_recording,
    _eval_voice_transcription,
    _eval_whiteboard,
)
from src.core.artifacts.federation import FederationArtifactBridge
from src.core.artifacts.models import ArtifactStatus, ArtifactType, VoiceCall
from src.core.artifacts.repository import create_artifact, create_voice_call
from src.core.artifacts.transcription import transcribe_call

# Sentinel marker used to make artifact ids created by this suite easy to
# find and clean up. It lives in the high range of snowflake space and is
# extremely unlikely to collide with real rows.
_ARTIFACTS_TEST_NONCE = 0x5A17E57


def _now_ms() -> int:
    return int(time.time() * 1000)


class ArtifactsEvalMixin:
    """Transcription-worker, federation-bridge, and eval-function suites."""

    ctx: Any

    def _check(self, label: str, path: str, cond: bool, error: str) -> bool: ...
    def _record(
        self,
        method: str,
        path: str,
        success: bool,
        label: str,
        error: Optional[str] = None,
        status_code: int = 200,
    ) -> None: ...

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
                "WORKER",
                "transcribe_call",
                False,
                "transcription_worker_error",
                sanitize_error(e),
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

            # --- Genuine HTTP exercise of the inbound federation routes ---
            # Create a real (encrypted) active connection, sign a payload with
            # its shared key, and POST to the actual /federation/artifact-op
            # and /artifact-event endpoints. They authenticate via HMAC over the
            # raw body and must return 200 (local delivery is best-effort and may
            # be 0 when no WebSocket dispatcher is attached). This proves the
            # routes are reachable and the signature verification works end to
            # end, rather than being silently skipped.
            import utils.encryption as _encryption

            shared_key = secrets.token_hex(16)
            enc_key = _encryption.encrypt_data(shared_key)
            remote_id = f"selftest-{secrets.token_hex(6)}"
            now_ms = int(time.time() * 1000)
            try:
                db.execute(
                    "INSERT INTO plexijoin_connections "
                    "(remote_instance_id, remote_url, shared_key_encrypted, "
                    "status, created_at, created_by) VALUES (?, ?, ?, 'active', ?, ?)",
                    (remote_id, "https://remote.example.invalid", enc_key, now_ms, 0),
                )
            except Exception as e:
                self._record(
                    "FEDERATION",
                    "create_connection",
                    False,
                    "federation_setup_error",
                    sanitize_error(e),
                )
                return

            try:
                session = self.ctx.session
                base = self.ctx.base_url

                op_payload = json.dumps(
                    {
                        "artifact_id": 123456789,
                        "op": {"op_type": "update", "field": "title", "value": "x"},
                        "actor_id": 0,
                    }
                ).encode("utf-8")
                # Inbound federation auth is HMAC over (body, timestamp,
                # nonce) with replay-nonce consumption: send all three
                # headers or verification 401s by design.
                from src.core.selftest.services.endpoints.federation import (
                    federation_headers,
                )

                op_resp = session.post(
                    f"{base}/api/v1/federation/artifact-op",
                    data=op_payload,
                    headers=federation_headers(shared_key, op_payload),
                    timeout=10,
                )
                self._check(
                    "federation_http_artifact_op",
                    "POST /api/v1/federation/artifact-op",
                    op_resp.status_code == 200,
                    f"expected 200, got {op_resp.status_code}: {op_resp.text[:200]}",
                )

                ev_payload = json.dumps(
                    {
                        "event_type": "update",
                        "artifact": {"id": 123456789, "title": "x"},
                    }
                ).encode("utf-8")
                ev_resp = session.post(
                    f"{base}/api/v1/federation/artifact-event",
                    data=ev_payload,
                    headers=federation_headers(shared_key, ev_payload),
                    timeout=10,
                )
                self._check(
                    "federation_http_artifact_event",
                    "POST /api/v1/federation/artifact-event",
                    ev_resp.status_code == 200,
                    f"expected 200, got {ev_resp.status_code}: {ev_resp.text[:200]}",
                )
            finally:
                try:
                    db.execute(
                        "DELETE FROM plexijoin_connections WHERE remote_instance_id = ?",
                        (remote_id,),
                    )
                except Exception:
                    pass

        except Exception as e:
            self._record(
                "FEDERATION",
                "FederationArtifactBridge",
                False,
                "federation_bridge_error",
                sanitize_error(e),
            )

    # === 8. individual _eval_* functions ===

    def _test_individual_eval(self) -> None:
        try:
            info = _eval_plexiscribe(
                {"enabled": True, "plexiscribe": {"enabled": True}}
            )
            self._check(
                "eval_plexiscribe_available",
                "_eval_plexiscribe",
                info.state
                in (
                    CapabilityState.AVAILABLE,
                    CapabilityState.DISABLED_BY_LICENSE,
                ),
                f"unexpected state: {info.state}",
            )

            info = _eval_plexiscribe(
                {"enabled": True, "plexiscribe": {"enabled": False}}
            )
            self._check(
                "eval_plexiscribe_disabled",
                "_eval_plexiscribe",
                info.state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected DISABLED_BY_CONFIG, got {info.state}",
            )

            info = _eval_plexiscript(
                {"enabled": True, "plexiscript": {"enabled": True}}
            )
            self._check(
                "eval_plexiscript_available",
                "_eval_plexiscript",
                info.state
                in (
                    CapabilityState.AVAILABLE,
                    CapabilityState.DISABLED_BY_LICENSE,
                ),
                f"unexpected state: {info.state}",
            )

            info = _eval_plexiscript(
                {"enabled": True, "plexiscript": {"enabled": False}}
            )
            self._check(
                "eval_plexiscript_disabled",
                "_eval_plexiscript",
                info.state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected DISABLED_BY_CONFIG, got {info.state}",
            )

            info = _eval_plexiboard({"enabled": True, "whiteboard": {"enabled": True}})
            self._check(
                "eval_plexiboard_available",
                "_eval_plexiboard",
                info.state
                in (
                    CapabilityState.AVAILABLE,
                    CapabilityState.DISABLED_BY_LICENSE,
                ),
                f"unexpected state: {info.state}",
            )

            info = _eval_plexiboard({"enabled": True, "whiteboard": {"enabled": False}})
            self._check(
                "eval_plexiboard_disabled",
                "_eval_plexiboard",
                info.state == CapabilityState.DISABLED_BY_CONFIG,
                f"expected DISABLED_BY_CONFIG, got {info.state}",
            )

            info = _eval_whiteboard({"enabled": True, "whiteboard": {"enabled": True}})
            self._check(
                "eval_whiteboard_available",
                "_eval_whiteboard",
                info.state
                in (
                    CapabilityState.AVAILABLE,
                    CapabilityState.DISABLED_BY_LICENSE,
                ),
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
            self._record(
                "EVAL", "_eval_*", False, "individual_eval_error", sanitize_error(e)
            )


def _recv_required_json(sock: Any, label: str) -> Dict[str, Any]:
    """Receive and decode one required JSON gateway frame with context."""
    try:
        raw = sock.recv()
    except Exception as exc:
        raise RuntimeError(f"{label}: websocket receive failed: {exc}") from exc
    if raw in (None, "", b""):
        raise RuntimeError(f"{label}: websocket closed without a JSON frame")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        preview = repr(raw[:200] if isinstance(raw, (str, bytes)) else raw)
        raise RuntimeError(f"{label}: invalid frame {preview}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label}: expected JSON object, got {type(value).__name__}")
    return value


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
