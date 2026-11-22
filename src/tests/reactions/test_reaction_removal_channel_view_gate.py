"""
Reaction removal/clear routes must enforce the same channel-view gate as the
add route.

The core manager gates reaction writes on participation (server membership)
plus ``messages.add_reactions`` / ``messages.manage``, but treats
``messages.read`` as a separate bit.  A member denied view by a channel
override is still a participant, so without an explicit route-level gate they
can remove or clear reactions in a channel they cannot see.  The add path
already has this gate; these tests pin it on every removal/clear path.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


_ENDPOINTS = ("remove_reaction", "clear_reactions", "clear_reactions_for_emoji")
_MANAGER_METHOD = {
    "remove_reaction": "remove_reaction",
    "clear_reactions": "remove_all_reactions",
    "clear_reactions_for_emoji": "remove_all_reactions_for_emoji",
}


def _ctx(user_id=1):
    return (
        SimpleNamespace(state=SimpleNamespace(is_internal=False)),
        SimpleNamespace(user_id=user_id),
    )


def _stubs(can_view):
    reactions = MagicMock()
    reactions.resolve_message_channel.return_value = {
        "channel_id": 50,
        "conversation_id": 20,
    }
    reactions.get_conversation_id_from_message.return_value = 20

    servers = MagicMock()
    servers.get_channel_by_conversation.return_value = {
        "id": 50,
        "server_id": 10,
    }

    def has_permission(uid, sid, perm, cid=None):
        if perm == "messages.read":
            return can_view
        return True

    servers.has_permission.side_effect = has_permission
    return reactions, servers


def _patches(reactions, servers):
    return (
        patch("src.api.get_reactions", return_value=reactions),
        patch("src.api.get_servers", return_value=servers),
        patch(
            "src.api.routes.reactions.ratelimit.acheck_rate_limit",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ),
        # The channel-id/message-id match is not what is under test here.
        patch("src.api.routes.reactions._validate_message_in_channel", return_value=50),
        # Drop fire-and-forget coroutines instead of scheduling them.
        patch("asyncio.create_task", side_effect=lambda coro: coro.close()),
        patch("src.core.events.gateway_emit.emit_message_reaction_remove_all"),
    )


async def _call(routes, endpoint, request, current_user):
    fn = getattr(routes, endpoint)
    if endpoint == "clear_reactions":
        return await fn("50", "60", request, current_user)
    return await fn("50", "60", "👍", request, current_user)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_removal_denied_without_channel_view(endpoint):
    from src.api.routes import reactions as routes

    reactions, servers = _stubs(can_view=False)
    request, current_user = _ctx()
    with ExitStack() as stack:
        for patcher in _patches(reactions, servers):
            stack.enter_context(patcher)
        with pytest.raises(HTTPException) as exc:
            await _call(routes, endpoint, request, current_user)

    assert exc.value.status_code == 403
    getattr(reactions, _MANAGER_METHOD[endpoint]).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_removal_denied_when_wired_channel_lookup_raises(endpoint):
    """A wired servers module whose reverse lookup fails must FAIL CLOSED."""
    from src.api.routes import reactions as routes

    reactions, servers = _stubs(can_view=True)
    # The module is wired and exposes the reverse-lookup API, but the lookup
    # itself blows up. The old `except: srv_channel = None` skipped the
    # messages.read check and allowed the mutation -- that is the fail-open.
    servers.get_channel_by_conversation.side_effect = RuntimeError("db exploded")
    request, current_user = _ctx()
    with ExitStack() as stack:
        for patcher in _patches(reactions, servers):
            stack.enter_context(patcher)
        with pytest.raises(HTTPException) as exc:
            await _call(routes, endpoint, request, current_user)

    assert exc.value.status_code == 403
    getattr(reactions, _MANAGER_METHOD[endpoint]).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_removal_denies_when_servers_module_unwired(endpoint):
    """An unwired servers module must FAIL CLOSED, not defer to the manager.

    A private-channel conversation_id alias can exist independently of the
    servers module, so skipping the view bit here would let a member denied
    messages.read remove/clear reactions in a channel they cannot see.
    """
    from src.api.routes import reactions as routes

    reactions, _unused = _stubs(can_view=False)
    request, current_user = _ctx()
    with ExitStack() as stack:
        # `None` means the reverse lookup cannot be evaluated at all.
        for patcher in _patches(reactions, None):
            stack.enter_context(patcher)
        with pytest.raises(HTTPException) as exc:
            await _call(routes, endpoint, request, current_user)

    assert exc.value.status_code == 403
    getattr(reactions, _MANAGER_METHOD[endpoint]).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_removal_defers_when_wired_stub_lacks_reverse_lookup(endpoint):
    """A wired manager stub without the reverse-lookup API still defers.

    This is the narrow pre-existing control: no channel view bit exists to
    check, so the manager's own fail-closed checks decide. Unwired (`None`)
    is deliberately NOT part of this exception -- see the deny test above.
    """
    from src.api.routes import reactions as routes

    reactions, _unused = _stubs(can_view=False)
    request, current_user = _ctx()
    with ExitStack() as stack:
        for patcher in _patches(reactions, object()):
            stack.enter_context(patcher)
        response = await _call(routes, endpoint, request, current_user)

    assert response.success is True
    getattr(reactions, _MANAGER_METHOD[endpoint]).assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_removal_allowed_with_channel_view(endpoint):
    from src.api.routes import reactions as routes

    reactions, servers = _stubs(can_view=True)
    request, current_user = _ctx()
    with ExitStack() as stack:
        for patcher in _patches(reactions, servers):
            stack.enter_context(patcher)
        response = await _call(routes, endpoint, request, current_user)

    assert response.success is True
    getattr(reactions, _MANAGER_METHOD[endpoint]).assert_called_once()
