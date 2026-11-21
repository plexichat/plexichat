"""Pin/unpin must not accept the channel's conversation id as a view-gate bypass.

``_resolve_channel_scope`` accepts both a channel id and the linked
conversation id (message routes use the latter to address server-channel
messages). The live ``channels.view`` gate added to the pin routes only probed
the URL id: when that id is the *conversation* id, ``channel_exists`` reports
no channel and the gate falls through as if it were a DM. A member denied view
on a private channel could then pin/unpin its messages by swapping the id in
the URL.

These regressions use a real server manager with a per-member
``channels.view`` deny override and assert both the rejection and that no
mutation happened.
"""

import pytest
from fastapi import HTTPException

import src.api as api_module
from src.api.routes.messages.pins import pin_message, unpin_message
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
    server = server_manager.create_server(owner.id, f"pin-alias-{id(server_manager)}")
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
        owner.id, channel.conversation_id, "private content"
    )
    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    monkeypatch.setattr(api_module, "_servers", server_manager)
    return {
        "owner": owner,
        "member": member,
        "channel": channel,
        "message": msg,
        "messaging": messaging_manager,
    }


def _pin_ids(env):
    return {
        p.id
        for p in env["messaging"].get_pinned_messages(
            env["owner"].id, env["channel"].conversation_id
        )
    }


@pytest.mark.asyncio
async def test_cannot_pin_via_conversation_id_when_view_denied(restricted_channel_env):
    env = restricted_channel_env
    channel, msg, member = env["channel"], env["message"], env["member"]

    with pytest.raises(HTTPException) as exc:
        await pin_message(
            str(channel.conversation_id),
            str(msg.id),
            current_user=_token(member.id, "m"),
        )
    assert exc.value.status_code in (403, 404)
    assert msg.id not in _pin_ids(env), (
        "must not mutate a channel the caller cannot view"
    )


@pytest.mark.asyncio
async def test_cannot_unpin_via_conversation_id_when_view_denied(
    restricted_channel_env,
):
    env = restricted_channel_env
    channel, msg, owner, member = (
        env["channel"],
        env["message"],
        env["owner"],
        env["member"],
    )
    assert (
        await pin_message(
            str(channel.id), str(msg.id), current_user=_token(owner.id, "owner")
        )
    ).success is True
    assert msg.id in _pin_ids(env)

    with pytest.raises(HTTPException) as exc:
        await unpin_message(
            str(channel.conversation_id),
            str(msg.id),
            current_user=_token(member.id, "m"),
        )
    assert exc.value.status_code in (403, 404)
    assert msg.id in _pin_ids(env), "the legitimate pin must remain"


@pytest.mark.asyncio
async def test_owner_can_still_pin_using_conversation_id_alias(restricted_channel_env):
    """The fix gates on the real channel, not on the URL id spelling."""
    env = restricted_channel_env
    channel, msg, owner = env["channel"], env["message"], env["owner"]

    response = await pin_message(
        str(channel.conversation_id),
        str(msg.id),
        current_user=_token(owner.id, "owner"),
    )
    assert response.success is True
    assert msg.id in _pin_ids(env)
