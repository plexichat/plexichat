"""SUM_SUBSCRIBE must gate the replay cursor at the same boundary as SUM_OP.

``SUM_OP`` parses ``base_revision`` through ``bounded_int`` (ASCII-only,
64-bit bounded) but the subscribe path used bare ``int()`` with ``max(0, ...)``.
That grammar accepts Unicode digit homoglyphs, PEP 515 separators, signs,
booleans and values past 2**63-1, and raises ``ValueError`` on a 5000-digit
token -- which the handler turned into 0, replaying every stored operation
instead of resuming. These tests pin the shared gate, the exact cursor
forwarded to replay, and that a refused frame never registers a subscription.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.api.websocket.handlers import sum as sum_handler
from src.api.websocket.handlers.sum import SumHandler
from src.api.websocket.opcodes import GatewayCloseCode

DECODE_ERROR = int(GatewayCloseCode.DECODE_ERROR)
WORKBOOK_ID = 9007199254740993


def _connection() -> SimpleNamespace:
    return SimpleNamespace(
        is_authenticated=True, user_id=1, connection_id="conn-revision-gate"
    )


def _install(monkeypatch) -> tuple[Mock, Mock, AsyncMock]:
    manager = Mock()
    manager.get_workbook.return_value = {"current_revision": 10}
    manager.replay_operations.return_value = []
    registry = Mock()
    registry.subscribe.return_value = True
    monkeypatch.setattr(sum_handler, "_get_sum_manager", lambda: manager)
    monkeypatch.setattr(sum_handler, "get_sum_subscription_registry", lambda: registry)
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(sum_handler, "send_sum_sync", sender)
    return manager, registry, sender


def _handler(monkeypatch) -> SumHandler:
    handler = SumHandler()
    monkeypatch.setattr(handler, "reauthorize_subscriptions", lambda _c: [])
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [
        "\u0665",  # Arabic-Indic digit: int() -> 5
        "\uff15",  # fullwidth digit: int() -> 5
        "1_0",  # PEP 515 separator: int() -> 10
        "-1",  # negative
        True,  # bool is not a revision number
        "99999999999999999999",  # past the signed 64-bit ceiling
        "9" * 5000,  # int() raises -> used to fall back to a full replay
        "garbage",  # int() raises -> used to fall back to a full replay
    ],
)
async def test_subscribe_rejects_non_canonical_revision(monkeypatch, malformed) -> None:
    manager, registry, _sender = _install(monkeypatch)
    close = await _handler(monkeypatch).handle_sum_subscribe(
        _connection(),
        {"workbook_id": WORKBOOK_ID, "base_revision": malformed},
        Mock(),
    )
    assert close == (None, None, DECODE_ERROR)
    manager.get_workbook.assert_not_called()
    manager.replay_operations.assert_not_called()
    registry.subscribe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), ("5", 5), (1.0, 1), (2**63 - 1, 2**63 - 1)],
)
async def test_subscribe_forwards_exact_canonical_revision(
    monkeypatch, value, expected
) -> None:
    manager, registry, _sender = _install(monkeypatch)
    close = await _handler(monkeypatch).handle_sum_subscribe(
        _connection(),
        {"workbook_id": WORKBOOK_ID, "base_revision": value},
        Mock(),
    )
    assert close == (None, None, None)
    manager.replay_operations.assert_called_once_with(
        1, WORKBOOK_ID, after_revision=expected
    )
    registry.subscribe.assert_called_once_with(1, WORKBOOK_ID, "conn-revision-gate")


@pytest.mark.asyncio
async def test_subscribe_defaults_to_zero_when_cursor_is_absent(monkeypatch) -> None:
    manager, _registry, _sender = _install(monkeypatch)
    await _handler(monkeypatch).handle_sum_subscribe(
        _connection(), {"workbook_id": WORKBOOK_ID}, Mock()
    )
    manager.replay_operations.assert_called_once_with(1, WORKBOOK_ID, after_revision=0)
