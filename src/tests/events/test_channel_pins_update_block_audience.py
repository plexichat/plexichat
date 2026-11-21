"""CHANNEL_PINS_UPDATE must not cross a two-way block.

``emit_channel_pins_update`` resolved its audience through the shared
``resolve_channel_event_recipients`` helper but never passed an ``actor_id``.
That helper hands an explicit ``user_ids`` list to ``dispatch``, so the
EventRouter's DM block gate never sees it; with no actor, its two-way block
filter is a no-op. A member who blocked the pinning user -- or whom the pinner
blocked -- therefore still received the actor's pin event merely for sharing a
channel/view permission.

The other channel-scoped emitters (typing, ACK, reactions, polls, bulk delete)
all thread an actor id through and are covered. This pins the last one.
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
BLOCKED = 4  # blocked BY the actor
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
        # Every member can view; the block gate is the only narrowing factor.
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


def test_pin_update_drops_actor_and_both_block_directions(env):
    captured, relationships = env

    gateway_emit.emit_channel_pins_update(
        CHANNEL, guild_id=GUILD, pinned_message_ids=[99], actor_id=ACTOR
    )

    assert captured["event_type"] is EventType.CHANNEL_PINS_UPDATE
    recipients = captured["kwargs"]["user_ids"]
    # Non-vacuous: the neutral viewer is still delivered to, both halves of
    # the block pair are withheld, and the actor's own echo is suppressed.
    assert recipients == [NEUTRAL]
    assert relationships.get_all_blocked_ids_bulk([ACTOR])[ACTOR] == [
        BLOCKER,
        BLOCKED,
    ]


def test_pin_update_without_actor_keeps_raw_viewer_set(env):
    """Pre-fix shape: proves the actor_id, not permission filtering, guards it."""
    captured, _ = env

    gateway_emit.emit_channel_pins_update(CHANNEL, guild_id=GUILD)

    recipients = captured["kwargs"]["user_ids"]
    assert recipients == [ACTOR, BLOCKER, NEUTRAL, BLOCKED]


def test_pin_update_fails_closed_when_block_set_unavailable(env, monkeypatch):
    captured, _ = env

    monkeypatch.setattr(
        api,
        "get_relationships",
        lambda: _Relationships({}, broken=True),
    )
    gateway_emit.clear_channel_recipient_cache()

    gateway_emit.emit_channel_pins_update(CHANNEL, guild_id=GUILD, actor_id=ACTOR)

    assert captured["kwargs"]["user_ids"] == [], (
        "unresolved block sets must not widen a channel-pin audience"
    )
