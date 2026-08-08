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
import json
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger

from src.api.websocket.opcodes import GatewayCloseCode, GatewayOpcode
from src.api.websocket.connection import Connection
from src.api.websocket.sum import (
    _get_sum_manager,
    get_sum_subscription_registry,
    relay_sum_op,
    send_sum_sync,
)
from src.core.suite.sum.manager import (
    SumConflictError,
    SumNotFoundError,
    SumPermissionError,
    SumValidationError,
)

MAX_OP_BYTES = 256 * 1024
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
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class SumHandler:
    """Handles PlexiSum real-time fabric opcodes."""

    def __init__(self) -> None:
        self._registry = get_sum_subscription_registry()

    def _manager(self):
        return _get_sum_manager()

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

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        workbook_id = _workbook_id(data)
        if workbook_id is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            manager = self._manager()
            workbook = manager.get_workbook(int(connection.user_id), workbook_id)
        except (SumPermissionError, SumNotFoundError, RuntimeError):
            logger.debug(
                f"User {connection.user_id} denied sum subscribe to workbook {workbook_id}"
            )
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)
        except Exception:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        self._registry.subscribe(
            connection.user_id, workbook_id, connection.connection_id
        )
        logger.debug(f"User {connection.user_id} subscribed to workbook {workbook_id}")

        base_revision = 0
        try:
            base_revision = max(0, int(data.get("base_revision") or 0))
        except (TypeError, ValueError):
            base_revision = 0

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

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        workbook_id = _workbook_id(data)
        op = data.get("op")
        if workbook_id is None or not isinstance(op, dict):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # Bound the op envelope: oversized payloads are rejected before any
        # parsing or persistence work happens.
        try:
            if len(json.dumps(op, ensure_ascii=False).encode("utf-8")) > MAX_OP_BYTES:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)
        except (TypeError, ValueError):
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
            sheet_id = int(sheet_id)
            base_revision = int(base_revision)
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
            # edit.
            try:
                workbook = manager.get_workbook(int(connection.user_id), workbook_id)
                revision = int(workbook.get("current_revision") or 0)
                replay = manager.replay_operations(
                    int(connection.user_id),
                    workbook_id,
                    after_revision=base_revision,
                )
                sync_payload = {
                    "workbook_id": workbook_id,
                    "revision": revision,
                    "ops": replay,
                }
                return int(GatewayOpcode.SUM_SYNC), sync_payload, None
            except Exception:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)
        except (SumPermissionError, SumNotFoundError):
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)
        except SumValidationError:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        except Exception as e:
            logger.warning(f"Sum op apply failed for workbook {workbook_id}: {e}")
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        # Relay the applied op to the workbook's other subscribers. The op is
        # idempotent by operation_id, so a relayed copy can never double-apply
        # on the server; recipients apply it locally to their grid.
        envelope: Dict[str, Any] = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "sheet_id": sheet_id,
            "base_revision": base_revision,
            "result_revision": int(
                result.get("revision") or result.get("result_revision") or 0
            ),
            "cells": cells,
        }
        try:
            await relay_sum_op(
                dispatcher=dispatcher,
                workbook_id=workbook_id,
                op=envelope,
                actor_id=int(connection.user_id),
                exclude_user_id=int(connection.user_id),
            )
        except Exception as e:
            logger.warning(f"Sum op relay failed for workbook {workbook_id}: {e}")

        return None, None, None
