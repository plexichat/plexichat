"""
PlexiSum handlers - Handle the Sum real-time fabric opcodes.

Registers handlers for:

- ``SUM_SUBSCRIBE`` (64): register the connection's user as a subscriber of a
  workbook, enforcing Sum domain authorization (owner / grant / membership)
  and the ``plexisum`` capability, then push a ``SUM_SYNC`` snapshot with the
  current revision plus op replay after the client's ``base_revision``.
- ``SUM_UNSUBSCRIBE`` (65): remove the subscription.
- ``SUM_OP`` (66): validate the op envelope, apply it through
  ``SumManager.apply_operation`` (idempotent by ``operation_id``), relay the
  op to the workbook's other subscribers, and on a revision conflict push a
  ``SUM_SYNC`` to the sender so it can rebase.

The REST API remains the authoritative persistence path; this fabric relays
deltas and snapshots on top of it.
"""

import asyncio
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger

from src.core.security.hardening import bounded_int
from src.api.websocket.boundaries import (
    MAX_OP_PAYLOAD_BYTES,
    parse_snowflake,
    payload_size,
)
from src.api.websocket.opcodes import GatewayCloseCode
from src.api.websocket.connection import Connection
from src.api.websocket.sum import (
    _capability_allows_sum,
    _get_sum_manager,
    get_sum_subscription_registry,
    prune_unauthorized_subscriptions,
    relay_sum_op,
    send_sum_sync,
)
from src.core.suite.sum.manager import (
    SumConflictError,
    SumNotFoundError,
    SumPermissionError,
    SumValidationError,
)

MAX_OP_BYTES = MAX_OP_PAYLOAD_BYTES
MAX_OP_CELLS = 2000
MAX_OPERATION_ID_LEN = 200


def _workbook_id(data: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return a validated integer workbook_id from a payload, or None."""
    if not data:
        return None
    raw = data.get("workbook_id")
    if raw is None:
        return None
    try:
        return parse_snowflake(raw, "workbook_id")
    except (TypeError, ValueError):
        return None


class SumHandler:
    """Handles PlexiSum real-time fabric opcodes."""

    def __init__(self) -> None:
        self._registry = get_sum_subscription_registry()

    def _manager(self):
        return _get_sum_manager()

    def reauthorize_subscriptions(self, connection: Connection) -> list:
        """Re-check every workbook subscription held by this connection.

        A Sum grant/scope can be revoked without tearing down the socket. Every
        Sum opcode calls this so a revoked grant is pruned on the next op; the
        relay path additionally re-checks recipients. Fails closed: when the
        capability or manager is unavailable, stale subscriptions are removed.
        """
        user_id = connection.user_id
        if not getattr(connection, "is_authenticated", False) or not user_id:
            return []

        def _authorized(workbook_id: int) -> bool:
            if not _capability_allows_sum():
                return False
            try:
                self._manager().get_workbook(int(user_id), int(workbook_id))
                return True
            except Exception:
                return False

        try:
            revoked = prune_unauthorized_subscriptions(
                self._registry,
                connection.connection_id,
                user_id,
                _authorized,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"Sum subscription re-authorization failed: {exc}")
            return []
        if revoked:
            logger.debug(
                f"Pruned {len(revoked)} no-longer-authorized workbook "
                f"subscriptions for connection {connection.connection_id}"
            )
        return revoked

    # === SUM_SUBSCRIBE =====================================================

    async def handle_sum_subscribe(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
        dispatcher: Any = None,
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle SUM_SUBSCRIBE opcode."""
        if not connection.is_authenticated or not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        self.reauthorize_subscriptions(connection)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        workbook_id = _workbook_id(data)
        if workbook_id is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # SUM_OP gates this same replay cursor through ``bounded_int``; keep the
        # subscribe path on the identical boundary. Bare ``int()`` accepted
        # Unicode digit homoglyphs (``"٥"`` -> 5), PEP 515 separators (``"1_0"``
        # -> 10), signs, booleans and values past 2**63-1, and its ValueError on
        # a 5000-digit token used to fall back to 0 -- silently replaying every
        # stored operation instead of resuming. Reject malformed cursors with
        # this opcode's DECODE_ERROR contract, before the subscription is
        # registered so a refused frame cannot leave a stale subscription.
        base_revision = 0
        raw_revision = data.get("base_revision")
        if raw_revision is not None:
            try:
                base_revision = bounded_int(
                    raw_revision, "base_revision", minimum=0, maximum=2**63 - 1
                )
            except (TypeError, ValueError):
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            manager = self._manager()
            workbook = manager.get_workbook(int(connection.user_id), workbook_id)
        except (SumPermissionError, SumNotFoundError, RuntimeError):
            logger.debug(
                f"User {connection.user_id} denied sum subscribe to workbook {workbook_id}"
            )
            # Resource-level denial: do NOT close the gateway with
            # NOT_AUTHENTICATED. The connection itself is authenticated;
            # killing it here traps clients in a ticket/identify/reconnect
            # loop when they merely reference a missing or forbidden workbook.
            return None, None, None
        except Exception:
            logger.error(
                f"Sum subscribe failed for user {connection.user_id}, "
                f"workbook {workbook_id}",
                exc_info=True,
            )
            return None, None, None

        if not self._registry.subscribe(
            connection.user_id, workbook_id, connection.connection_id
        ):
            logger.warning(
                f"Connection {connection.connection_id} exhausted its workbook "
                f"subscription budget; subscribe ignored"
            )
            return None, None, None
        logger.debug(f"User {connection.user_id} subscribed to workbook {workbook_id}")

        # Replay persisted operations after the client's last acknowledged
        # revision so a late joiner can catch up without refetching cells.
        ops: list = []
        try:
            ops = manager.replay_operations(
                int(connection.user_id), workbook_id, after_revision=base_revision
            )
        except Exception as e:
            logger.debug(f"Sum subscribe replay failed for {workbook_id}: {e}")

        rate_limit_per_minute = getattr(dispatcher, "_rate_limit_per_minute", None)
        try:
            revision = int(workbook.get("current_revision") or 0)
            sent = await send_sum_sync(
                connection,
                workbook_id,
                revision,
                ops,
                rate_limit_per_minute=rate_limit_per_minute,
            )
            if not sent:
                # The sync was rejected (rate limit or send failure): do not
                # leave a half-registered subscription behind that would
                # silently miss later deltas.
                self._registry.unsubscribe(
                    connection.user_id, workbook_id, connection.connection_id
                )
        except Exception as e:
            logger.debug(f"Sum sync send failed for {workbook_id}: {e}")
            self._registry.unsubscribe(
                connection.user_id, workbook_id, connection.connection_id
            )

        return None, None, None

    # === SUM_UNSUBSCRIBE ===================================================

    async def handle_sum_unsubscribe(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle SUM_UNSUBSCRIBE opcode."""
        if not connection.is_authenticated or not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        self.reauthorize_subscriptions(connection)

        workbook_id = _workbook_id(data)
        if workbook_id is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        self._registry.unsubscribe(
            connection.user_id, workbook_id, connection.connection_id
        )
        logger.debug(
            f"User {connection.user_id} unsubscribed from workbook {workbook_id}"
        )
        return None, None, None

    # === SUM_OP ============================================================

    async def handle_sum_op(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
        dispatcher: Any,
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle SUM_OP opcode by applying and relaying the operation."""
        if not connection.is_authenticated or not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        self.reauthorize_subscriptions(connection)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        workbook_id = _workbook_id(data)
        op = data.get("op")
        if workbook_id is None or not isinstance(op, dict):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # Bound the op envelope: oversized payloads are rejected before any
        # parsing or persistence work happens.
        if payload_size(op) > MAX_OP_BYTES:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        operation_id = op.get("operation_id")
        operation_type = op.get("operation_type")
        sheet_id = op.get("sheet_id")
        base_revision = op.get("base_revision")
        cells = op.get("cells")

        if (
            not isinstance(operation_id, str)
            or not operation_id
            or len(operation_id) > MAX_OPERATION_ID_LEN
        ):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        if not isinstance(operation_type, str) or not operation_type:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        if sheet_id is None or base_revision is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        try:
            sheet_id = parse_snowflake(sheet_id, "sheet_id")
            base_revision = bounded_int(
                base_revision, "base_revision", minimum=0, maximum=2**63 - 1
            )
        except (TypeError, ValueError):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        if not isinstance(cells, list) or not cells or len(cells) > MAX_OP_CELLS:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            manager = self._manager()
        except Exception as e:
            logger.warning(f"Sum manager unavailable for workbook {workbook_id}: {e}")
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        try:
            result = await asyncio.to_thread(
                manager.apply_operation,
                int(connection.user_id),
                workbook_id,
                sheet_id,
                operation_id,
                operation_type,
                base_revision,
                cells,
            )
        except SumConflictError:
            # The sender's base revision is stale. Push a SUM_SYNC containing
            # the current revision plus every operation after the sender's
            # base so the client can rebase and re-apply rather than lose the
            # edit. The reply goes through ``send_sum_sync`` so it honors the
            # same per-connection rate limiting as every other fabric send;
            # returning the frame for the gateway to inline would let an
            # attacker spam stale ops to force unbounded snapshot builds.
            try:
                workbook = manager.get_workbook(int(connection.user_id), workbook_id)
                revision = int(workbook.get("current_revision") or 0)
                replay = manager.replay_operations(
                    int(connection.user_id),
                    workbook_id,
                    after_revision=base_revision,
                )
                await send_sum_sync(
                    connection,
                    workbook_id,
                    revision,
                    replay,
                    rate_limit_per_minute=getattr(
                        dispatcher, "_rate_limit_per_minute", None
                    ),
                )
                return None, None, None
            except Exception:
                logger.error(
                    f"Sum conflict sync failed for workbook {workbook_id}",
                    exc_info=True,
                )
                return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)
        except (SumPermissionError, SumNotFoundError):
            # Resource-level denial on op apply: drop the op without closing
            # the gateway (NOT_AUTHENTICATED here causes an auth reconnect
            # loop for what is only a permission problem).
            logger.debug(
                f"User {connection.user_id} denied sum op on workbook {workbook_id}; dropping"
            )
            return None, None, None
        except SumValidationError:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        except Exception as e:
            logger.warning(f"Sum op apply failed for workbook {workbook_id}: {e}")
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        # Relay the applied op to the workbook's other subscribers. The op is
        # idempotent by operation_id, so a relayed copy can never double-apply
        # on the server; recipients apply it locally to their grid.
        # Broadcast POST-ACK: derive the sequence numbers from the acked
        # result revision instead of echoing the sender's (possibly stale)
        # enqueue-time base_revision. A client flushing a queue of ops
        # enqueued at one revision would otherwise stamp every relayed frame
        # with the same old base, forcing spurious SUM_SYNC round-trips.
        applied_revision = int(
            result.get("revision") or result.get("result_revision") or 0
        )
        envelope: Dict[str, Any] = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "sheet_id": sheet_id,
            "base_revision": max(0, applied_revision - 1),
            "result_revision": applied_revision,
            "cells": cells,
        }
        # Relay to the workbook's other subscribers. Skip only the ORIGINATING
        # connection: the actor's other tabs are distinct subscribers and must
        # receive the delta (ops are idempotent by operation_id, so applying
        # as a remote edit can never double-apply). Excluding the whole user
        # left those tabs stale until reload.
        try:
            await relay_sum_op(
                dispatcher=dispatcher,
                workbook_id=workbook_id,
                op=envelope,
                actor_id=int(connection.user_id),
                exclude_connection_id=connection.connection_id,
            )
        except Exception as e:
            logger.warning(f"Sum op relay failed for workbook {workbook_id}: {e}")

        return None, None, None
