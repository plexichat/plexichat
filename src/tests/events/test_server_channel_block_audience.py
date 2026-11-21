"""Server-channel channel-scoped gateway events must not cross a block.

``resolve_channel_event_recipients`` applies the two-way block filter to the
DM and private-thread audiences (via ``_dm_participants_without_blocks``), but
the server-channel branch handed the raw ``channels.view`` viewer set straight
to ``dispatch`` as an explicit ``user_ids`` list. The EventRouter's DM block
gate never sees that explicit audience, so a member who blocked the actor --
or whom the actor blocked -- still received the actor's reaction-clear, poll
and bulk-delete frames merely for sharing a server/channel. The REST typing and
read-receipt routes already apply this gate at their own level; the shared
resolver used by the gateway emitters was the missing choke point.
"""

from types import SimpleNamespace

import pytest

import src.api as api
from src.core.events import gateway_emit
from src.core.events.types import EventType

CHANNEL = 333
GUILD = 222
ACTOR = 1
BLOCKER = 2  # blocked the actor
BLOCKED = 4  # blocked by the actor
NEUTRAL = 3

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _threads_unavailable(monkeypatch):
    """Force the private-thread fast path off so the server branch is hit."""
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: False)


class _Servers:
    def get_channel(self, cid, actor_id):
        return SimpleNamespace(server_id=GUILD)

    def get_member_user_ids(self, sid):
        return [ACTOR, BLOCKER, NEUTRAL, BLOCKED]

    def has_permission(self, uid, sid, permission, cid):
        # Every member can view; the block gate is the only thing that must
        # narrow this audience.
        return True


class _Relationships:
    def __init__(self, blocks, *, broken=False):
        self._blocks = blocks
        self._broken = broken

    def get_all_blocked_ids_bulk(self, ids):
        if self._broken:
            raise RuntimeError("relationship store unavailable")
        return {int(uid): list(self._blocks.get(int(uid), ())) for uid in ids}


def _wire(monkeypatch, relationships, captured):
    monkeypatch.setattr(api, "get_servers", lambda: _Servers())
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_relationships", lambda: relationships)
    monkeypatch.setattr(
        gateway_emit,
        "_emit",
        lambda event_type, data, **kwargs: captured.update(
            event_type=event_type, kwargs=kwargs
        ),
    )
    gateway_emit.clear_channel_recipient_cache()


@pytest.fixture
def env(monkeypatch):
    relationships = _Relationships(
        {
            ACTOR: [BLOCKER, BLOCKED],
            BLOCKER: [ACTOR],
            BLOCKED: [ACTOR],
            NEUTRAL: [],
        }
    )
    captured = {}
    _wire(monkeypatch, relationships, captured)
    yield captured, relationships
    gateway_emit.clear_channel_recipient_cache()


@pytest.mark.parametrize(
    "emit,event_type",
    [
        (
            lambda: gateway_emit.emit_message_reaction_remove_all(
                CHANNEL, 99, guild_id=GUILD, actor_id=ACTOR
            ),
            EventType.MESSAGE_REACTION_REMOVE_ALL,
        ),
        (
            lambda: gateway_emit.emit_poll_update(
                10, 99, "vote", actor_id=ACTOR, channel_id=CHANNEL
            ),
            EventType.POLL_UPDATE,
        ),
        (
            lambda: gateway_emit.emit_message_delete_bulk(
                CHANNEL, [99], guild_id=GUILD, actor_id=ACTOR
            ),
            EventType.MESSAGE_DELETE_BULK,
        ),
    ],
)
def test_server_channel_event_drops_actor_and_blocked_viewers(env, emit, event_type):
    captured, relationships = env

    emit()

    assert captured["event_type"] is event_type
    recipients = captured["kwargs"]["user_ids"]
    # The actor is excluded and both halves of a block pair are withheld; the
    # unrelated viewer still receives the event.
    assert recipients == [NEUTRAL]
    assert relationships.get_all_blocked_ids_bulk([ACTOR])[ACTOR] == [BLOCKER, BLOCKED]


def test_server_channel_event_fails_closed_when_block_set_unavailable(env, monkeypatch):
    captured, _ = env

    monkeypatch.setattr(
        api,
        "get_relationships",
        lambda: _Relationships({}, broken=True),
    )
    gateway_emit.clear_channel_recipient_cache()

    gateway_emit.emit_poll_update(10, 99, "vote", actor_id=ACTOR, channel_id=CHANNEL)

    assert captured["kwargs"]["user_ids"] == [], (
        "unresolved block sets must not widen a server-channel audience"
    )
