"""GET /channels/{id}/pins must reject the channel's conversation-id alias.

The live ``_verify_pinned_channel_access`` gate probes only the URL id with
``get_channel``/``channel_exists``. A server channel's ``conversation_id`` is
a legal alias in the message routes (``_resolve_channel_scope`` accepts both
forms), but it is not a channel id: both probes miss it, so the gate treated
the alias as a DM and let the cached pin list through. A member denied
``channels.view`` on a private channel could read its pinned messages by
putting the conversation id in the URL.

The gate must resolve the addressed id membership-agnostically and, when it
backs a server channel, require a live ``get_channel`` grant for that caller.
"""

import pytest
from fastapi import HTTPException

import src.api as api_module
from src.api.routes.messages.messages_list import _verify_pinned_channel_access
from src.core.auth.models import AccountType, TokenInfo


def _token(uid):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user{uid}",
        account_type=AccountType.USER,
    )


@pytest.fixture
def restricted_channel_env(
    db, messaging_manager, server_manager, two_users, monkeypatch
):
    owner, member = two_users
    server = server_manager.create_server(
        owner.id, f"pins-list-alias-{id(server_manager)}"
    )
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(owner.id, server.id, "secret", "text")
    server_manager.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=member.id,
        deny={"channels.view": True},
    )
    assert server_manager.get_channel(channel.id, member.id) is None
    msg = messaging_manager.send_message(
        owner.id, channel.conversation_id, "private pin"
    )
    messaging_manager.pin_message(owner.id, msg.id)
    # Sanity: the alias really is backed by a server channel, so this test
    # cannot pass just because the fixture lost its conversation link.
    assert (
        server_manager.get_channel_by_conversation(channel.conversation_id) is not None
    )
    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    monkeypatch.setattr(api_module, "_servers", server_manager)
    return {"owner": owner, "member": member, "channel": channel, "message": msg}


@pytest.mark.asyncio
async def test_conversation_id_alias_denies_member_without_view(restricted_channel_env):
    env = restricted_channel_env
    with pytest.raises(HTTPException) as exc:
        await _verify_pinned_channel_access(
            str(env["channel"].conversation_id), _token(env["member"].id)
        )
    assert exc.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_conversation_id_alias_allowed_for_viewer(restricted_channel_env):
    """The fix gates on the real channel, not on the URL id spelling."""
    env = restricted_channel_env
    await _verify_pinned_channel_access(
        str(env["channel"].conversation_id), _token(env["owner"].id)
    )


@pytest.mark.asyncio
async def test_channel_id_still_allowed_for_viewer(restricted_channel_env):
    env = restricted_channel_env
    await _verify_pinned_channel_access(str(env["channel"].id), _token(env["owner"].id))
