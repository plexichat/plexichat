"""DM message-broadcast block filter must fail CLOSED.

``_resolve_recipients`` hands the dispatcher an explicit ``user_ids`` audience,
so the EventRouter's implicit DM block gate never sees it. The two-way block
filter is therefore applied explicitly on this path. It used to swallow an
error from that filter ("DM block filter unavailable") and return the
UNFILTERED conversation-participant list -- delivering the actor's
MESSAGE_CREATE/UPDATE/DELETE events to users who blocked them (or whom they
blocked) whenever the authoritative block lookup could not be resolved.

Withholding delivery on an unverifiable audience is the only safe behaviour;
the analogous reaction-emitter path already fails closed the same way.
"""

from types import SimpleNamespace

import pytest

import src.core.events.gateway_emit as gateway_emit
from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients

ACTOR = 11
BLOCKER = 22
BLOCKED = 33


class _Messaging:
    """DM conversation source that records whether the fallback ran."""

    def __init__(self, participants):
        self.participants = participants
        self.calls = []

    def get_participants(self, actor_id, conversation_id):
        self.calls.append((actor_id, conversation_id))
        return [SimpleNamespace(user_id=uid) for uid in self.participants]


@pytest.mark.messaging
def test_dm_broadcast_withholds_audience_when_block_filter_unavailable(monkeypatch):
    def explode(_participants, _actor_id):
        raise RuntimeError("authoritative block store unavailable")

    monkeypatch.setattr(gateway_emit, "_dm_participants_without_blocks", explode)

    messaging = _Messaging([ACTOR, BLOCKER, BLOCKED])

    ids, server_id = _resolve_recipients(None, messaging, 55, 55, actor_id=ACTOR)

    assert messaging.calls == [(ACTOR, 55)], "DM participant fallback never ran"
    assert ids == [], "an unresolved block filter must widen the audience to nobody"
    assert server_id is None


@pytest.mark.messaging
def test_dm_broadcast_healthy_filter_still_delivers_unblocked_audience(monkeypatch):
    """Non-vacuous control: the failing test above is not silencing all traffic."""

    def filter_blocks(participants, _actor_id):
        return [uid for uid in participants if uid not in (BLOCKER, BLOCKED)]

    monkeypatch.setattr(gateway_emit, "_dm_participants_without_blocks", filter_blocks)

    messaging = _Messaging([ACTOR, BLOCKER, BLOCKED])

    ids, server_id = _resolve_recipients(None, messaging, 55, 55, actor_id=ACTOR)

    assert ids == [ACTOR]
    assert server_id is None
