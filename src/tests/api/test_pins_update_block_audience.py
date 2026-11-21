"""Live pin/unpin routes must pass the actor into CHANNEL_PINS_UPDATE fan-out.

The shared emitter ``emit_channel_pins_update`` applies the two-way block
filter only when it receives an ``actor_id``. This guards the route wiring: a
blocked viewer must not receive the pinning user's channel event, while an
unrelated viewer still does. Both pin and unpin are covered, and each test
first proves (with no block) that the blocked viewer *would* otherwise be in
the audience -- so the assertion cannot pass vacuously.
"""

import pytest

import src.api as api_module
from src.api.routes.messages.pins import pin_message, unpin_message
from src.core.auth.models import AccountType, TokenInfo
from src.core.events import gateway_emit
from src.core.events.types import EventType

pytestmark = [pytest.mark.asyncio]


class _Relationships:
    """Minimal two-way block source for the resolver's bulk lookup."""

    def __init__(self, blocks=None):
        self._blocks = blocks or {}

    def get_all_blocked_ids_bulk(self, ids):
        return {int(uid): list(self._blocks.get(int(uid), ())) for uid in ids}


def _token_info(user_id, username):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=username,
        account_type=AccountType.USER,
    )


@pytest.fixture
def env(db, messaging_manager, server_manager, three_users, monkeypatch):
    owner, blocked, neutral = three_users
    server = server_manager.create_server(
        owner.id, f"pin-audience-{id(server_manager)}"
    )
    server_manager.add_member(server.id, blocked.id)
    server_manager.add_member(server.id, neutral.id)
    channel = server_manager.create_channel(owner.id, server.id, "general", "text")

    try:
        from src.core.servers.manager import backfill_default_role_permissions

        backfill_default_role_permissions(db)
    except (ImportError, AttributeError):
        try:
            server_manager.backfill_default_role_permissions(db)
        except AttributeError:
            pass

    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    monkeypatch.setattr(api_module, "_servers", server_manager)
    # Keep this test focused on the CHANNEL_PINS_UPDATE emitter; the route's
    # other fire-and-forget MESSAGE_UPDATE broadcast needs no live gateway.
    import src.api.websocket as ws_module

    monkeypatch.setattr(ws_module, "is_setup", lambda: False)
    return {
        "owner": owner,
        "blocked": blocked,
        "neutral": neutral,
        "server": server,
        "channel": channel,
        "messaging": messaging_manager,
    }


@pytest.fixture
def captured(monkeypatch):
    real_emitter = gateway_emit.emit_channel_pins_update
    state = {"emitter_kwargs": [], "pin_audiences": []}

    def spy(**kwargs):
        state["emitter_kwargs"].append(kwargs)
        return real_emitter(**kwargs)

    def fake_emit(event_type, data, *, user_ids=None, **kwargs):
        if event_type is EventType.CHANNEL_PINS_UPDATE:
            state["pin_audiences"].append(list(user_ids or []))

    monkeypatch.setattr(gateway_emit, "emit_channel_pins_update", spy)
    monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
    gateway_emit.clear_channel_recipient_cache()
    yield state
    gateway_emit.clear_channel_recipient_cache()


def _set_relationships(monkeypatch, blocks):
    monkeypatch.setattr(api_module, "_relationships", _Relationships(blocks))
    gateway_emit.clear_channel_recipient_cache()


def _assert_route_actor_and_audience(env, state, actor):
    assert len(state["emitter_kwargs"]) == 1
    call = state["emitter_kwargs"][0]
    assert call.get("actor_id") == actor.id, (
        "route must pass the acting user to the emitter"
    )
    assert call["channel_id"] == env["channel"].id
    assert len(state["pin_audiences"]) == 1
    return state["pin_audiences"][0]


async def test_pin_route_filters_blocked_pair(env, captured, monkeypatch):
    owner, blocked, neutral = env["owner"], env["blocked"], env["neutral"]
    msg = env["messaging"].send_message(owner.id, env["channel"].conversation_id, "pin")

    # 1. No block: the blocked-to-be viewer IS in the audience (non-vacuous).
    _set_relationships(monkeypatch, {})
    response = await pin_message(
        str(env["channel"].id), str(msg.id), current_user=_token_info(owner.id, "owner")
    )
    assert response.success is True
    audience = _assert_route_actor_and_audience(env, captured, owner)
    assert set(audience) == {blocked.id, neutral.id}

    # 2. Two-way block: same route/actor, but the blocked viewer is withheld.
    captured["emitter_kwargs"].clear()
    captured["pin_audiences"].clear()
    _set_relationships(monkeypatch, {owner.id: [blocked.id], blocked.id: [owner.id]})
    response = await pin_message(
        str(env["channel"].id), str(msg.id), current_user=_token_info(owner.id, "owner")
    )
    assert response.success is True
    audience = _assert_route_actor_and_audience(env, captured, owner)
    assert audience == [neutral.id]


async def test_unpin_route_filters_blocked_pair(env, captured, monkeypatch):
    owner, blocked, neutral = env["owner"], env["blocked"], env["neutral"]
    msg = env["messaging"].send_message(
        owner.id, env["channel"].conversation_id, "unpin"
    )
    await pin_message(
        str(env["channel"].id), str(msg.id), current_user=_token_info(owner.id, "owner")
    )
    captured["emitter_kwargs"].clear()
    captured["pin_audiences"].clear()

    # 1. No block: the blocked-to-be viewer IS in the audience (non-vacuous).
    _set_relationships(monkeypatch, {})
    response = await unpin_message(
        str(env["channel"].id), str(msg.id), current_user=_token_info(owner.id, "owner")
    )
    assert response.success is True
    audience = _assert_route_actor_and_audience(env, captured, owner)
    assert set(audience) == {blocked.id, neutral.id}

    # 2. Two-way block: the blocked viewer no longer receives the event.
    captured["emitter_kwargs"].clear()
    captured["pin_audiences"].clear()
    _set_relationships(monkeypatch, {owner.id: [blocked.id], blocked.id: [owner.id]})
    response = await unpin_message(
        str(env["channel"].id), str(msg.id), current_user=_token_info(owner.id, "owner")
    )
    assert response.success is True
    audience = _assert_route_actor_and_audience(env, captured, owner)
    assert audience == [neutral.id]
