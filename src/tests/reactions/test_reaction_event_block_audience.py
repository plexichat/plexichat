"""Reaction gateway events must not reach a user in a block relationship.

Every other ephemeral event fanned out with an explicit audience (typing,
read receipts, DM message events) applies the two-way block filter because the
dispatcher's ``dispatch_event`` takes a pre-resolved ``user_ids`` list and
bypasses the EventRouter's DM block gate. The reaction route was the odd one
out: it excluded only the acting user, so a participant who blocked the actor
-- or whom the actor blocked -- still received MESSAGE_REACTION_ADD/REMOVE
frames for that actor's reactions.

This pins the gate at the reaction dispatch helper: the actor is excluded and
blocked participants are dropped, with a fail-closed audience when the block
set cannot be resolved.
"""

from __future__ import annotations

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes import reactions as reactions_routes

pytestmark = pytest.mark.unit

ACTOR = 1
BLOCKER = 2  # blocked the actor
BLOCKED = 4  # blocked by the actor
NEUTRAL = 3

CONVERSATION_ID = 55
MESSAGE_ID = 99
CHANNEL_ID = 42


class _Relationships:
    """Two-way block sets keyed by user; no bulk helper on purpose."""

    def __init__(self, blocks):
        self._blocks = blocks
        self.calls = []

    def get_all_blocked_ids(self, user_id):
        self.calls.append(user_id)
        return list(self._blocks.get(int(user_id), ()))


class _Reactions:
    def get_conversation_id_from_message(self, message_id):
        return CONVERSATION_ID

    def get_participant_ids(self, conversation_id):
        return [ACTOR, BLOCKER, NEUTRAL, BLOCKED]


class _Recorder:
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


@pytest.fixture
def env(monkeypatch):
    recorder = _Recorder()
    # ACTOR <-> BLOCKER and ACTOR <-> BLOCKED are two-way block pairs.
    relationships = _Relationships(
        {
            ACTOR: [BLOCKER, BLOCKED],
            BLOCKER: [ACTOR],
            BLOCKED: [ACTOR],
            NEUTRAL: [],
        }
    )
    monkeypatch.setattr(reactions_routes.api, "get_reactions", lambda: _Reactions())
    monkeypatch.setattr(api_module, "get_relationships", lambda: relationships)
    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: recorder)
    return recorder, relationships


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,event_type",
    [
        ("add", "MESSAGE_REACTION_ADD"),
        ("remove", "MESSAGE_REACTION_REMOVE"),
        ("remove_emoji", "MESSAGE_REACTION_REMOVE_EMOJI"),
    ],
)
async def test_reaction_event_drops_actor_and_blocked_participants(
    env, kind, event_type
):
    recorder, relationships = env

    await reactions_routes._dispatch_reaction_event(
        kind, ACTOR, MESSAGE_ID, CHANNEL_ID, "👍"
    )

    assert len(recorder.dispatched) == 1
    event, recipients = recorder.dispatched[0]
    assert event.event_type.value == event_type
    assert recipients == [NEUTRAL]
    assert relationships.calls, "two-way block gate was never consulted"


@pytest.mark.asyncio
async def test_reaction_event_fails_closed_when_block_set_unavailable(env, monkeypatch):
    recorder, _ = env

    def explode(_user_id):
        raise RuntimeError("relationships store unavailable")

    monkeypatch.setattr(
        api_module,
        "get_relationships",
        lambda: type("Broken", (), {"get_all_blocked_ids": staticmethod(explode)})(),
    )

    await reactions_routes._dispatch_reaction_event(
        "add", ACTOR, MESSAGE_ID, CHANNEL_ID, "👍"
    )

    assert recorder.dispatched == [], (
        "unresolved block sets must not widen the audience"
    )
