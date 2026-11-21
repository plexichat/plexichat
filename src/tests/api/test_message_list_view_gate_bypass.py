"""The channel message-list route must not leak a private channel's messages.

``get_channel_messages`` first asks the servers manager, which enforces
``channels.view``. When that raises ``ChannelNotFoundError`` the route falls
back to the messaging service using the URL id as a conversation id. For a
server channel the messaging ``is_participant`` check grants access to every
server member (no ``channels.view`` gate), so a member denied view can list the
channel's messages by addressing it with its conversation-id alias.

These regressions use a real server manager with a per-member
``channels.view`` deny override and assert that the private page is not served.
"""

import pytest
from fastapi import HTTPException

import src.api as api_module
from src.api.routes.messages.messages_list import get_channel_messages
from src.api.routes.messages.render_cache import get_render_cache
from src.core.auth.models import AccountType, TokenInfo


def _token(uid, name):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=name,
        account_type=AccountType.USER,
    )


@pytest.fixture
def restricted_channel_env(
    db, messaging_manager, server_manager, two_users, monkeypatch
):
    owner, member = two_users
    server = server_manager.create_server(owner.id, f"msglist-{id(server_manager)}")
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(owner.id, server.id, "secret", "text")
    server_manager.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=member.id,
        deny={"channels.view": True},
    )
    # The canonical accessor enforces channels.view: the member cannot see it.
    assert server_manager.get_channel(channel.id, member.id) is None
    assert server_manager.get_channel(channel.id, owner.id) is not None
    msg = messaging_manager.send_message(
        owner.id, channel.conversation_id, "private content"
    )
    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    monkeypatch.setattr(api_module, "_servers", server_manager)
    get_render_cache().clear()
    return {
        "owner": owner,
        "member": member,
        "channel": channel,
        "message": msg,
    }


async def _list(uid, name, channel_id):
    # Call the undecorated endpoint: Query(...) defaults are not usable when
    # bypassing FastAPI, so pass the pagination parameters explicitly.
    return await get_channel_messages.__wrapped__(
        str(channel_id),
        limit=50,
        before=None,
        after=None,
        current_user=_token(uid, name),
    )


@pytest.mark.asyncio
async def test_member_denied_view_cannot_list_via_conversation_id_alias(
    restricted_channel_env,
):
    env = restricted_channel_env
    channel = env["channel"]

    with pytest.raises(HTTPException) as exc:
        await _list(env["member"].id, "m", channel.conversation_id)
    assert exc.value.status_code == 404, (
        "a member denied channels.view must not read the channel's message list"
    )


@pytest.mark.asyncio
async def test_member_denied_view_cannot_list_via_channel_id(restricted_channel_env):
    """The gate also covers the plain channel-id URL spelling."""
    env = restricted_channel_env
    channel = env["channel"]

    with pytest.raises(HTTPException) as exc:
        await _list(env["member"].id, "m", channel.id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_owner_can_still_list_via_conversation_id_alias(restricted_channel_env):
    """The gate keys on view permission, not on the URL id spelling."""
    env = restricted_channel_env
    channel = env["channel"]

    result = await _list(env["owner"].id, "owner", channel.conversation_id)
    assert any(getattr(m, "content", None) == "private content" for m in result), (
        "the owner (with channels.view) must still see the messages"
    )
