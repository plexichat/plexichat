"""Regression: VOICE_SPEAKING must carry a per-connection inbound ceiling.

A single authenticated socket may otherwise stream VOICE_SPEAKING frames
without limit.  That opcode is one of the heaviest on the inbound path:

* it calls ``signaling.record_activity`` on every frame, which REFRESHES the
  signaling idle clock -- so the stale-connection sweeper cannot be the bound
  (the exact opposite: a flooder keeps itself alive forever);
* it runs an O(channel-members) threadpool DB visibility walk;
* it performs two recipient fan-outs (voice state + speaking indicator).

``INBOUND_RATE_LIMITS`` is the only per-connection frame-rate guard on that
path.  This test drives an over-budget burst through ``OpcodeHandler.handle``
and proves the excess frames are dropped *before* the heavy handler runs.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.handlers import INBOUND_RATE_LIMITS, OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager

pytestmark = pytest.mark.websocket


def _authenticated_connection() -> SimpleNamespace:
    return SimpleNamespace(
        connection_id="speaking-flood-conn",
        _inbound_rate_buckets={},
        is_authenticated=True,
        is_selftest=False,
        user_id=42,
    )


@pytest.mark.asyncio
async def test_voice_speaking_burst_is_capped_before_the_heavy_handler(
    monkeypatch,
) -> None:
    """Over-budget VOICE_SPEAKING frames never reach the dispatch path."""
    op = int(GatewayOpcode.VOICE_SPEAKING)
    assert op in INBOUND_RATE_LIMITS, (
        "VOICE_SPEAKING mutates voice state, refreshes the signaling idle "
        "clock (defeating the stale sweeper) and fans out to every channel "
        "member; it must be throttled per connection"
    )
    budget, window = INBOUND_RATE_LIMITS[op]
    assert budget > 0 and window > 0

    handler = OpcodeHandler(session_manager=SessionManager())
    speaking_handler = AsyncMock(return_value=(None, None, None))
    monkeypatch.setattr(
        handler._voice_handler, "handle_voice_speaking", speaking_handler
    )

    conn = _authenticated_connection()
    sent = budget + 25
    for _ in range(sent):
        outcome = await handler.handle(
            conn,
            op,
            {"channel_id": "123", "speaking": True, "volume": 1.0},
        )
        # A throttled frame is dropped silently: no close, no error response.
        assert outcome == (None, None, None)

    assert speaking_handler.await_count == budget, (
        f"{sent} speaking frames reached the heavy handler "
        f"{speaking_handler.await_count} times; expected at most {budget}"
    )
    # The budget is not zero: legitimate speaking indicators still flow.
    assert speaking_handler.await_count > 0
