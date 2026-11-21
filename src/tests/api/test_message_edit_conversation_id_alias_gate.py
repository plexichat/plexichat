"""PATCH /channels/{id}/messages/{mid} must reject a server channel's
conversation-id alias when the caller cannot view that channel.

Message routes accept the channel's linked ``conversation_id`` as a URL alias
(``_resolve_channel_scope`` admits both spellings), so a member denied
``channels.view`` on a private channel can still address a message there by
putting the conversation id in the URL. ``get_channel``/``channel_exists`` only
recognise channel ids and would treat the alias as a DM, and the core edit path
only checks participation (granted to every server member) plus authorship --
neither is a channel-view boundary. Without a live, alias-aware gate the author
of a message can keep editing it after losing view access to the channel.

Regression for the edit route, which (unlike delete/get/pins) had no live
``channels.view`` gate at all. Run with:
    pytest src/tests/api/test_message_edit_conversation_id_alias_gate.py -q -n 0
"""

import uuid

import pytest
from fastapi import HTTPException

import src.api as api_module
from src.api.routes.messages.messages_crud.composer import MessagesCRUDRouter
from src.api.schemas.messages import MessageUpdateRequest
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
        owner.id, "edit-alias-" + uuid.uuid4().hex[:6]
    )
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(
        owner.id, server.id, "secret-edit-" + uuid.uuid4().hex[:6], "text"
    )
    conv_id = int(channel.conversation_id)

    # Both users author a message while the channel is still viewable. The
    # member's own message is the edit target after view access is revoked.
    member_msg = messaging_manager.send_message(member.id, conv_id, "member original")
    owner_msg = messaging_manager.send_message(owner.id, conv_id, "owner original")

    # Owner denies the member channels.view on this private channel.
    server_manager.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=member.id,
        deny={"channels.view": True},
    )

    # Preconditions: member is still a participant (membership != view), cannot
    # resolve the channel, but the alias is genuinely backed by a server channel.
    assert messaging_manager.is_participant(conv_id, member.id) is True
    assert server_manager.get_channel(channel.id, member.id) is None
    assert server_manager.get_channel_by_conversation(conv_id) is not None

    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    monkeypatch.setattr(api_module, "_servers", server_manager)
    return {
        "owner": owner,
        "member": member,
        "channel": channel,
        "conv_id": conv_id,
        "member_msg": member_msg,
        "owner_msg": owner_msg,
        "messaging": messaging_manager,
    }


def _content(messaging, user_id, msg_id):
    return messaging.get_message(user_id, msg_id).content


@pytest.mark.asyncio
async def test_view_denied_author_cannot_edit_via_conversation_id_alias(
    restricted_channel_env,
):
    env = restricted_channel_env
    router = MessagesCRUDRouter()
    with pytest.raises(HTTPException) as exc:
        await router.edit_message(
            str(env["conv_id"]),
            str(env["member_msg"].id),
            MessageUpdateRequest(content="pwned via alias"),
            _token(env["member"].id),
        )
    assert exc.value.status_code in (403, 404)
    assert (
        _content(env["messaging"], env["member"].id, env["member_msg"].id)
        == "member original"
    )


@pytest.mark.asyncio
async def test_view_denied_author_cannot_edit_via_channel_id(restricted_channel_env):
    env = restricted_channel_env
    router = MessagesCRUDRouter()
    with pytest.raises(HTTPException) as exc:
        await router.edit_message(
            str(env["channel"].id),
            str(env["member_msg"].id),
            MessageUpdateRequest(content="pwned via channel id"),
            _token(env["member"].id),
        )
    assert exc.value.status_code in (403, 404)
    assert (
        _content(env["messaging"], env["member"].id, env["member_msg"].id)
        == "member original"
    )


@pytest.mark.asyncio
async def test_viewer_can_still_edit_via_conversation_id_alias(restricted_channel_env):
    """Non-vacuous: the gate keys on the real channel, not the URL spelling."""
    env = restricted_channel_env
    router = MessagesCRUDRouter()
    result = await router.edit_message(
        str(env["conv_id"]),
        str(env["owner_msg"].id),
        MessageUpdateRequest(content="legitimate edit via alias"),
        _token(env["owner"].id),
    )
    assert result.content == "legitimate edit via alias"
    assert (
        _content(env["messaging"], env["owner"].id, env["owner_msg"].id)
        == "legitimate edit via alias"
    )
