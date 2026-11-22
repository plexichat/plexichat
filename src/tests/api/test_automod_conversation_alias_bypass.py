"""Regression: server automod must also gate sends addressed by a server
channel's linked ``conversation_id`` alias.

``POST /channels/{id}/messages`` accepts the linked conversation id in the
``channel_id`` slot. On that path ``_resolve_server_id`` returns None (the alias
is not a ``srv_channels`` row id), so the REST route's ``if server_id:`` block
-- the only place it calls ``automod.check_message_fast`` -- is skipped. The
alias fallback in ``_send_message_with_fallback`` does check a live view grant,
``messages.send`` and (since N673) slowmode via ``_require_alias_send_access``,
but never applied the synchronous automod gate. A member could therefore post
content that every keyword/regex/spam/mention/link rule would reject simply by
swapping the channel id for its conversation id.

The fix must run the same fast automod check against the REAL backing channel
and server id, and fail closed when that alias cannot be resolved (the normal
per-channel id path is never reached, so there is nothing else to fall back
to). Genuine DM/group conversations have no backing channel and stay unaffected.

Run with: pytest src/tests/api/test_automod_conversation_alias_bypass.py -q -n 0
"""

import uuid

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.messages.messages_crud.composer import MessagesCRUDRouter
from src.api.schemas.messages import MessageCreateRequest
from src.core.auth.models import AccountType, TokenInfo

pytestmark = pytest.mark.messaging

BLOCKED = "aliased-scam-keyword"


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


async def _noop_coro(*args, **kwargs):
    return None


@pytest.fixture
def automod_alias(modules, user_pool, monkeypatch):
    from src.core import automod
    from src.core.automod import RuleType

    automod.setup(
        modules._db, modules.servers, modules.messaging, modules.notifications
    )
    try:
        owner = user_pool.get_user()
        member = user_pool.get_user()
        servers = modules.servers
        messaging = modules.messaging

        server = servers.create_server(
            owner_id=owner.id, name="automod-alias-" + uuid.uuid4().hex[:6]
        )
        servers.add_member(server.id, member.id)
        channel = servers.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name="automod-alias-" + uuid.uuid4().hex[:6],
        )
        conv_id = int(channel.conversation_id)

        rule = automod.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="alias keyword filter",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": [BLOCKED],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )
        assert rule is not None

        # The send grant removes every other reason a send could be denied, so
        # automod is the only thing that may stop either request.
        servers.set_channel_override(
            owner.id, channel.id, "member", member.id, allow={"messages.send": True}, deny={}
        )

        monkeypatch.setattr(api, "get_servers", lambda: servers)
        monkeypatch.setattr(api, "get_messaging", lambda: messaging)
        monkeypatch.setattr(api, "get_auth", lambda: None)
        monkeypatch.setattr(api, "get_media", lambda: None)

        router = MessagesCRUDRouter()
        monkeypatch.setattr(router, "_broadcast_message_create", _noop_coro)

        yield router, servers, messaging, channel, conv_id, member, server
    finally:
        automod._manager = None
        automod._setup_complete = False


class TestAutomodConversationAliasBypass:
    def test_preconditions_alias_is_not_a_channel_and_send_is_granted(
        self, automod_alias
    ):
        """Pin the facts that make the bypass possible."""
        _router, servers, messaging, channel, conv_id, member, server = automod_alias
        assert servers.get_channel(conv_id, member.id) is None
        assert int(servers.get_channel_by_conversation(conv_id)["id"]) == int(channel.id)
        assert servers.has_permission(member.id, server.id, "messages.send", channel.id)
        assert messaging.is_participant(conv_id, member.id) is True

    @pytest.mark.asyncio
    async def test_channel_id_send_is_blocked_by_automod(self, automod_alias):
        """Control: the normal channel-id path enforces the keyword rule."""
        router, _servers, messaging, channel, conv_id, member, _server = automod_alias
        with pytest.raises(HTTPException) as exc:
            await router.send_channel_message(
                str(channel.id), MessageCreateRequest(content=BLOCKED), _token(member.id)
            )
        assert exc.value.status_code == 400
        assert [m.content for m in (messaging.get_messages(member.id, conv_id, limit=10) or [])] == []

    @pytest.mark.asyncio
    async def test_alias_send_is_blocked_by_automod(self, automod_alias):
        """A send through the conversation-id alias must hit the same rule."""
        router, _servers, messaging, _channel, conv_id, member, _server = automod_alias
        with pytest.raises(HTTPException) as exc:
            await router.send_channel_message(
                str(conv_id), MessageCreateRequest(content=BLOCKED), _token(member.id)
            )
        assert exc.value.status_code == 400, (
            "automod must be enforced through the conversation-id alias"
        )
        assert [m.content for m in (messaging.get_messages(member.id, conv_id, limit=10) or [])] == []

    @pytest.mark.asyncio
    async def test_alias_send_fails_closed_when_automod_gate_unavailable(
        self, automod_alias, monkeypatch
    ):
        """If the automod gate cannot run on the alias path, deny the send.

        The alias path has no second automod check to fall back to, so an
        unavailable moderation service must not silently grant the send.
        """
        from src.core import automod

        router, _servers, messaging, _channel, conv_id, member, _server = automod_alias

        def _boom(*args, **kwargs):
            raise RuntimeError("automod service unavailable")

        monkeypatch.setattr(automod, "check_message_fast", _boom)
        with pytest.raises(HTTPException) as exc:
            await router.send_channel_message(
                str(conv_id),
                MessageCreateRequest(content="harmless text"),
                _token(member.id),
            )
        assert exc.value.status_code == 403
        assert [m.content for m in (messaging.get_messages(member.id, conv_id, limit=10) or [])] == []

    @pytest.mark.asyncio
    async def test_genuine_dm_is_not_blocked_by_automod_alias_gate(
        self, automod_alias, user_pool
    ):
        """A real DM has no backing channel; server automod must not deny it."""
        router, _servers, messaging, _channel, _conv_id, member, _server = automod_alias
        other = user_pool.get_user()
        conversation = messaging.create_dm(member.id, other.id)
        dm_id = int(conversation.id)
        assert messaging.is_participant(dm_id, member.id) is True

        response = await router.send_channel_message(
            str(dm_id),
            MessageCreateRequest(content=BLOCKED),
            _token(member.id),
        )
        assert response is not None
        assert [m.content for m in (messaging.get_messages(member.id, dm_id, limit=10) or [])] == [BLOCKED]
