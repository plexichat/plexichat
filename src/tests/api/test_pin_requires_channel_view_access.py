"""Pin/unpin must enforce channel-view access on the addressed channel.

Security regression: the pin route bound the message to the URL channel via
``_resolve_channel_scope`` + ``_ensure_message_in_channel`` and relied on
``messaging.get_message``/``pin_message`` participant checks. For server
channels those participant checks reduce to *server membership* — they do not
consult ``channels.view``. A member denied view on a private channel could
therefore pin/unpin messages inside it (integrity violation, plus a pin
broadcast/pin-list surface for a channel they cannot see).

The route must require the same ``get_channel`` access check the message-list
route uses before it mutates anything.
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
    """A server channel the member cannot view, with one message in it."""
    owner, member = two_users
    server = server_manager.create_server(owner.id, f"pin-view-{id(server_manager)}")
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(owner.id, server.id, "secret", "text")

    # Member is in the server but is barred from this specific channel.
    server_manager.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=member.id,
        deny={"channels.view": True},
    )
    assert (
        server_manager.has_permission(member.id, server.id, "channels.view", channel.id)
        is False
    )
    # Sanity: the canonical channel accessor enforces the restriction.
    assert server_manager.get_channel(channel.id, member.id) is None

    msg = messaging_manager.send_message(
        owner.id, channel.conversation_id, "top secret"
    )

    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    monkeypatch.setattr(api_module, "_servers", server_manager)

    return {
        "owner": owner,
        "member": member,
        "server": server,
        "channel": channel,
        "message": msg,
        "messaging": messaging_manager,
    }


def _is_pinned(env, msg):
    """Read pin state directly from the pin store (owner is a participant)."""
    pins = env["messaging"].get_pinned_messages(
        env["owner"].id, env["channel"].conversation_id
    )
    return any(getattr(p, "id", None) == msg.id for p in pins)


@pytest.mark.asyncio
async def test_member_without_channels_view_cannot_pin_message(restricted_channel_env):
    env = restricted_channel_env
    member, channel, msg = env["member"], env["channel"], env["message"]

    with pytest.raises(HTTPException) as exc:
        await pin_message(
            str(channel.id), str(msg.id), current_user=_token(member.id, "member")
        )
    assert exc.value.status_code in (403, 404)

    # The mutation must never have happened.
    assert not _is_pinned(env, msg)


@pytest.mark.asyncio
async def test_member_without_channels_view_cannot_unpin_message(
    restricted_channel_env,
):
    env = restricted_channel_env
    owner, member, channel, msg = (
        env["owner"],
        env["member"],
        env["channel"],
        env["message"],
    )
    # Owner legitimately pins it first.
    assert (
        await pin_message(
            str(channel.id), str(msg.id), current_user=_token(owner.id, "owner")
        )
    ).success is True
    assert _is_pinned(env, msg)

    with pytest.raises(HTTPException) as exc:
        await unpin_message(
            str(channel.id), str(msg.id), current_user=_token(member.id, "member")
        )
    assert exc.value.status_code in (403, 404)

    assert _is_pinned(env, msg)
