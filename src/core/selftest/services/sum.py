"""
PlexiSum self-test service for SelfTestRunner.

Exercises the PlexiSum realtime fabric end-to-end without leaving dangling
connections, rows, or workbook files behind:

- Capability gate: the suite first resolves the ``plexisum`` capability; when
  it is not fully available (config or licence), the realtime round-trip is
  skipped with a visible reason instead of failing the whole self-test run.
- REST fixture: creates a temporary workbook through the live SumManager,
  grants the second self-test user ``view`` access, and applies one operation
  so the workbook has revision history.
- WSS harness: a real gateway handshake (HELLO -> IDENTIFY) for two self-test
  connections. The subscriber sends ``SUM_SUBSCRIBE`` (opcode 64) and asserts
  the ``SUM_SYNC`` (opcode 67) bootstrap carries the current revision and the
  replayed op. The actor then sends ``SUM_OP`` (opcode 66) and the subscriber
  asserts the op is relayed back. Connections are closed and the workbook is
  deleted in a finally block so no ports or rows are left dangling.

The suite uses the live server database (:func:`src.api.get_db`) and the live
WebSocket gateway, so it runs cleanly inside ``main.py self-test``.
"""

import json
from typing import Any, Dict, Optional

import websocket

import src.api as api
import utils.logger as logger

from src.api.websocket.opcodes import GatewayOpcode
from src.core.artifacts.capabilities import get_capability, is_usable
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables

from ..context import SelfTestContext

# Sentinel marker used to make workbook titles created by this suite easy to
# find and clean up. It lives in the high range of snowflake space and is
# extremely unlikely to collide with real rows.
_SUM_TEST_NONCE = 0x50A15F7


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


def _recv_json_with_timeout(
    sock: Any, timeout: float = 5.0
) -> Optional[Dict[str, Any]]:
    """Receive one JSON frame with a socket timeout; None on timeout."""
    sock.settimeout(timeout)
    try:
        raw = sock.recv()
    except Exception:
        return None
    finally:
        sock.settimeout(None)
    if raw in (None, "", b""):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class SumTester:
    """Self-test suite covering the PlexiSum feature and its realtime fabric."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

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
            logger.info(f"[sum] {label}: PASS")
        else:
            logger.error(f"[sum] {label}: FAIL - {error}")

    def _check(self, label: str, path: str, cond: bool, error: str) -> bool:
        self._record("sum", path, bool(cond), label, None if cond else error)
        return bool(cond)

    def test_sum(self) -> None:
        """Run the PlexiSum self-test suite."""
        if not self._capability_allows_sum():
            self._record(
                "sum",
                "/api/v1/suite/sum/workbooks",
                True,
                "sum_skipped_capability",
                "plexisum capability not available (config/licence); skipping",
            )
            return
        self._test_realtime_round_trip()

    def _capability_allows_sum(self) -> bool:
        try:
            return is_usable(get_capability("plexisum").state)
        except Exception:  # pragma: no cover - defensive
            return False

    def _test_realtime_round_trip(self) -> None:
        if not self.ctx.token or not self.ctx.other_token:
            self._record(
                "sum",
                "/gateway sum",
                False,
                "sum_wss_setup",
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
        manager: Optional[SumManager] = None
        owner_id = self.ctx.test_user_id or 1
        temporary_workbook_id: Optional[int] = None
        try:
            if db is None:
                raise RuntimeError("Database unavailable for Sum WSS fixture")
            create_sum_tables(db)
            manager = SumManager(db, servers_module=api.get_servers())
            other_id = self.ctx.test_other_user_id or 2

            workbook = manager.create_workbook(
                owner_id,
                f"selftest-sum-{_SUM_TEST_NONCE}",
                scope="personal",
            )
            temporary_workbook_id = int(workbook["id"])
            sheet_id = int(workbook["sheets"][0]["id"])

            # Grant the subscriber read access so the relay revalidation and
            # subscribe authorization both succeed.
            manager.grant_workbook(owner_id, temporary_workbook_id, other_id, ["view"])

            # Give the workbook revision history so the SUM_SYNC bootstrap has
            # something to replay.
            manager.apply_operation(
                owner_id,
                temporary_workbook_id,
                sheet_id,
                f"selftest-op-{_SUM_TEST_NONCE}",
                "set_cell",
                0,
                [{"row": 0, "column": 0, "value_type": "number", "value": 42}],
            )

            # Subscriber connection (other_user) -- the client that should
            # receive the relayed op.
            sub_ws = websocket.create_connection(ws_url, timeout=5, header=headers)
            hello = _recv_required_json(sub_ws, "subscriber HELLO")
            if hello.get("op") != int(GatewayOpcode.HELLO):
                self._record(
                    "sum",
                    "/gateway sum",
                    False,
                    "sum_wss_sub_hello",
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
                                "device": "selftest-sum-sub",
                            },
                        },
                    }
                )
            )
            sub_ready = _recv_required_json(sub_ws, "subscriber READY")
            if sub_ready.get("t") != "READY":
                self._record(
                    "sum",
                    "/gateway sum",
                    False,
                    "sum_wss_sub_identify",
                    f"subscriber expected READY, got {sub_ready.get('t')}",
                )
                return

            # Actor connection (main user) -- sends the SUM_OP.
            actor_ws = websocket.create_connection(ws_url, timeout=5, header=headers)
            hello2 = _recv_required_json(actor_ws, "actor HELLO")
            if hello2.get("op") != int(GatewayOpcode.HELLO):
                self._record(
                    "sum",
                    "/gateway sum",
                    False,
                    "sum_wss_actor_hello",
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
                                "device": "selftest-sum-actor",
                            },
                        },
                    }
                )
            )
            actor_ready = _recv_required_json(actor_ws, "actor READY")
            if actor_ready.get("t") != "READY":
                self._record(
                    "sum",
                    "/gateway sum",
                    False,
                    "sum_wss_actor_identify",
                    f"actor expected READY, got {actor_ready.get('t')}",
                )
                return

            # Subscriber subscribes to the workbook; the gateway must reply
            # with a SUM_SYNC bootstrap carrying the current revision and the
            # previously applied op.
            sub_ws.send(
                json.dumps(
                    {
                        "op": int(GatewayOpcode.SUM_SUBSCRIBE),
                        "d": {
                            "workbook_id": temporary_workbook_id,
                            "base_revision": 0,
                        },
                    }
                )
            )
            sync = _recv_json_with_timeout(sub_ws, timeout=5)
            if sync is None:
                self._record(
                    "sum",
                    "/gateway sum",
                    False,
                    "sum_wss_subscribe_sync",
                    "subscriber received no frame after SUM_SUBSCRIBE",
                )
                return
            sync_d = sync.get("d", {})
            # Gateway frames carry ids as strings; compare stringified so a
            # serialization choice never reads as a protocol failure.
            sync_ok = (
                sync.get("op") == int(GatewayOpcode.SUM_SYNC)
                and str(sync_d.get("workbook_id")) == str(temporary_workbook_id)
                and int(sync_d.get("revision") or 0) >= 1
                and isinstance(sync_d.get("ops"), list)
                and len(sync_d.get("ops", [])) == 1
                and sync_d["ops"][0].get("operation_id")
                == f"selftest-op-{_SUM_TEST_NONCE}"
            )
            if not self._check(
                "sum_wss_subscribe_sync",
                "/gateway sum",
                sync_ok,
                f"unexpected SUM_SYNC bootstrap: {sync}",
            ):
                return

            # Actor sends a SUM_OP (e.g. a set_cell delta).
            actor_ws.send(
                json.dumps(
                    {
                        "op": int(GatewayOpcode.SUM_OP),
                        "d": {
                            "workbook_id": temporary_workbook_id,
                            "op": {
                                "operation_id": f"selftest-relay-{_SUM_TEST_NONCE}",
                                "operation_type": "set_cell",
                                "sheet_id": sheet_id,
                                "base_revision": 1,
                                "cells": [
                                    {
                                        "row": 1,
                                        "column": 1,
                                        "value_type": "text",
                                        "value": "round-trip-marker",
                                    }
                                ],
                            },
                        },
                    }
                )
            )

            # The subscriber should receive the relayed SUM_OP.
            relayed = _recv_json_with_timeout(sub_ws, timeout=5)
            if relayed is None:
                self._record(
                    "sum",
                    "/gateway sum",
                    False,
                    "sum_wss_relay_received",
                    "subscriber received no frame after SUM_OP",
                )
                return

            d = relayed.get("d", {})
            relayed_op = d.get("op", {})
            ok = (
                relayed.get("op") == int(GatewayOpcode.SUM_OP)
                and str(d.get("workbook_id")) == str(temporary_workbook_id)
                and str(d.get("actor_id")) == str(owner_id)
                and isinstance(relayed_op, dict)
                and relayed_op.get("operation_id")
                == f"selftest-relay-{_SUM_TEST_NONCE}"
                and relayed_op.get("result_revision") == 2
            )
            self._record(
                "sum",
                "/gateway sum",
                ok,
                "sum_wss_relay_received",
                None if ok else f"unexpected relayed frame: {relayed}",
                status_code=101 if ok else 0,
            )

            # Unsubscribe for clean teardown.
            try:
                sub_ws.send(
                    json.dumps(
                        {
                            "op": int(GatewayOpcode.SUM_UNSUBSCRIBE),
                            "d": {"workbook_id": temporary_workbook_id},
                        }
                    )
                )
            except Exception:
                pass
        except Exception as e:
            self._record(
                "sum",
                "/gateway sum",
                False,
                "sum_wss_error",
                str(e),
            )
        finally:
            for sock in (sub_ws, actor_ws):
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
            if manager is not None and temporary_workbook_id is not None:
                try:
                    manager.delete_workbook(owner_id, temporary_workbook_id)
                except Exception as exc:
                    logger.debug(
                        "Failed to clean temporary Sum workbook %s: %s",
                        temporary_workbook_id,
                        exc,
                    )
