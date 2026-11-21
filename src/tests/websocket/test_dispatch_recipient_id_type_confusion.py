"""Gateway fan-out must not launder malformed recipient ids into principals.

``GatewayDispatcher.dispatch_event`` and ``dispatch_raw_to_users`` are the
fan-out boundary for explicit-audience event delivery. Several production
callers hand them lists built straight from raw DB projections -- e.g.
``read_receipts.py`` passes ``get_participant_ids()`` (the unfiltered
``msg_participants.user_id`` projection) into ``dispatch_event``. The boundary
used to coerce every value with ``int()``, so a malformed-but-coercible id
(``"05"`` -> 5, ``7.9`` -> 7, ``True`` -> 1) was admitted as a genuine recipient
that never proved membership or view rights, leaking events to the wrong
principal. Only values that are already genuine ``int`` may be dispatched.
"""

from unittest.mock import MagicMock

import pytest

from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.opcodes import GatewayOpcode
from src.core.events.models import Event
from src.core.events.types import EventType


def _dispatcher_recording_ids():
    """Dispatcher whose session manager records the post-coercion id list."""
    dispatcher = GatewayDispatcher(session_manager=MagicMock())
    seen = []

    def _capture(user_ids):
        seen.append(list(user_ids))
        return []  # no connections -> both fan-outs return early

    dispatcher._session_manager.get_connections_for_users.side_effect = _capture
    return dispatcher, seen


# Raw projection-style audience: genuine 7 and 42 plus coercible junk that
# int() would launder into 5, 8 and 1.
_RAW = ["05", 7, " 8 ", True, 5.9, 42]


@pytest.mark.asyncio
async def test_dispatch_event_rejects_coerced_recipient_ids():
    dispatcher, seen = _dispatcher_recording_ids()
    event = Event(event_type=EventType.TYPING_START, data={})

    await dispatcher.dispatch_event(event, _RAW)

    assert seen == [[7, 42]]
    for wrong in (5, 8, 1):
        assert wrong not in seen[0], f"laundered recipient id {wrong} admitted"


@pytest.mark.asyncio
async def test_dispatch_raw_rejects_coerced_recipient_ids():
    dispatcher, seen = _dispatcher_recording_ids()

    await dispatcher.dispatch_raw_to_users(
        _RAW, GatewayOpcode.VOICE_SPEAKING, {"speaking": True}
    )

    assert seen == [[7, 42]]
    for wrong in (5, 8, 1):
        assert wrong not in seen[0], f"laundered recipient id {wrong} admitted"


@pytest.mark.asyncio
async def test_dispatch_event_preserves_all_genuine_ints():
    # Non-vacuous: a clean all-int audience must pass through intact.
    dispatcher, seen = _dispatcher_recording_ids()
    event = Event(event_type=EventType.TYPING_START, data={})

    await dispatcher.dispatch_event(event, [10, 20, 30, 20])

    assert seen == [[10, 20, 30, 20]]
