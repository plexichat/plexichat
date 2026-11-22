"""
``add_reaction`` must enforce the channel-view gate even when servers is unwired.

The core manager gates reaction writes on participation plus
``messages.add_reactions``, but treats view access (``messages.read``) as a
separate bit.  The removal/clear paths call ``_require_channel_view``, which
fails closed when the servers module is unwired.  The add path instead used an
inline ``if servers_mod and hasattr(servers_mod, ...)`` gate that SKIPPED
entirely when servers was ``None`` -- a fail-open that let a user add a reaction
to a private-channel message without the view check.

These tests pin add to the canonical helper and prove, via a control that
neutralizes the gate, that the harness really reaches the manager write (so the
403 is caused by the gate, not by an unrelated early rejection).
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_CID = "50"
_MID = "60"


def _ctx(user_id=1):
    return (
        SimpleNamespace(state=SimpleNamespace(is_internal=False)),
        SimpleNamespace(user_id=user_id),
    )


def _stubs():
    """A reactions store where the message resolves to a private channel."""
    reactions = MagicMock()
    reactions.resolve_message_channel.return_value = {
        "channel_id": 50,
        "conversation_id": 20,
    }
    reactions.get_conversation_id_from_message.return_value = 20
    return reactions


def _patches(reactions, servers_mod):
    return (
        patch("src.api.get_reactions", return_value=reactions),
        patch("src.api.get_servers", return_value=servers_mod),
        patch(
            "src.api.routes.reactions.ratelimit.acheck_rate_limit",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ),
        patch("src.api.routes.reactions._invalidate_reaction_read_cache"),
        patch("asyncio.create_task", side_effect=lambda coro: coro.close()),
    )


async def _call(routes, request, current_user):
    return await routes.add_reaction(_CID, _MID, "👍", request, current_user)


@pytest.mark.asyncio
async def test_add_denies_when_servers_module_unwired():
    """An unwired servers module must FAIL CLOSED, not skip the view gate.

    A private-channel conversation_id alias can exist independently of the
    servers module, so skipping the reverse lookup here let a member denied
    messages.read add a reaction to a message in a channel they cannot see.
    """
    from src.api.routes import reactions as routes

    reactions = _stubs()
    request, current_user = _ctx()
    with ExitStack() as stack:
        # None == the view lookup cannot be evaluated at all.
        for patcher in _patches(reactions, None):
            stack.enter_context(patcher)
        with pytest.raises(HTTPException) as exc:
            await _call(routes, request, current_user)

    assert exc.value.status_code == 403
    reactions.add_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_add_write_reachable_when_gate_neutralized():
    """Control: with the view gate neutralized the unwired request writes.

    This proves the harness actually reaches ``reactions.add_reaction``; the
    403 in the deny test is therefore attributable to the fail-closed gate and
    a fail-open add path would have performed the write.
    """
    from src.api.routes import reactions as routes

    reactions = _stubs()
    request, current_user = _ctx()
    with ExitStack() as stack:
        for patcher in _patches(reactions, None):
            stack.enter_context(patcher)
        stack.enter_context(patch("src.api.routes.reactions._require_channel_view"))
        response = await _call(routes, request, current_user)

    assert response.success is True
    reactions.add_reaction.assert_called_once()
