"""Query-count regression tests for channel resolution on send/edit.

BEFORE this change the REST send path resolved the same channel row two to
three times per request: once in ``_resolve_server_id`` (server id + timeout
check), again in ``_send_message_with_fallback`` (conversation link), and a
third time via ``get_channel_conversation_id`` when building the automod
context. The edit path resolved the conversation twice as well. Each of those
is a database round-trip on the request's critical path.

A single per-request ``_channel_ctx`` now carries the resolved channel through
the helper chain. These tests pin the query counts so the fan-out cannot creep
back in, while proving the no-context (legacy) behaviour and the failed-lookup
fallback are unchanged.
"""

import pytest

from src.api.routes.messages.messages_crud.composer import MessagesCRUDRouter


class _Channel:
    def __init__(self, server_id, conversation_id):
        self.server_id = server_id
        self.conversation_id = conversation_id


class _Servers:
    def __init__(self, channel):
        self.channel = channel
        self.get_channel_calls = 0
        self.get_conv_calls = 0

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls += 1
        return self.channel

    def get_channel_conversation_id(self, channel_id):
        self.get_conv_calls += 1
        return self.channel.conversation_id if self.channel else None


class _Message:
    id = 5001
    conversation_id = 777
    author_id = 2
    content = "hello"


class _Messaging:
    def __init__(self):
        self.send_calls = []

    def send_message(self, **kwargs):
        self.send_calls.append(kwargs)
        return _Message()

    def get_messages(self, user_id, conversation_id, limit=50):
        return []


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_send_reuses_one_resolved_channel():
    router = MessagesCRUDRouter()
    servers = _Servers(_Channel(server_id=5, conversation_id=777))
    messaging = _Messaging()
    ctx = {}

    server_id = router._resolve_server_id(10, 2, servers, ctx)
    assert server_id == 5
    assert servers.get_channel_calls == 1

    router._build_automod_context(
        10, 2, "hi", servers, messaging, "message_create", ctx
    )
    # The cached channel's conversation_id was used, not a fresh lookup.
    assert servers.get_conv_calls == 0

    msg, resolved_server_id = await router._send_message_with_fallback(
        10, 2, "hi", None, None, None, servers, messaging, ctx
    )
    assert resolved_server_id == 5
    assert msg is not None
    # Still exactly one get_channel() for the whole chain.
    assert servers.get_channel_calls == 1
    assert messaging.send_calls[0]["conversation_id"] == 777


def test_without_ctx_keeps_legacy_lookup_behaviour():
    router = MessagesCRUDRouter()
    servers = _Servers(_Channel(server_id=5, conversation_id=777))
    messaging = _Messaging()

    assert router._resolve_server_id(10, 2, servers) == 5
    assert servers.get_channel_calls == 1

    router._build_automod_context(10, 2, "hi", servers, messaging, "message_create")
    assert servers.get_conv_calls == 1


def test_failed_channel_lookup_is_not_cached_preserving_fallback():
    class _FailingServers(_Servers):
        def get_channel(self, channel_id, user_id):
            self.get_channel_calls += 1
            raise RuntimeError("db down")

    router = MessagesCRUDRouter()
    servers = _FailingServers(None)
    ctx = {}

    assert router._resolve_server_id(10, 2, servers, ctx) is None
    # A failed lookup must not be cached so the later helper re-attempts it
    # and can still surface an access-denied error as a 403.
    assert "channel" not in ctx
    assert servers.get_channel_calls == 1
