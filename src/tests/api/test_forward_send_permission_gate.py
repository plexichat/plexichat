"""SECURITY: forwarding must enforce ``messages.send`` on the target channel.

The forward route publishes the new message through ``messaging.send_message``,
which does NOT apply the REST send route's ``messages.send`` (or timeout) gate.
Conversation participation and ``channels.view`` are both granted to a member
who may only *read* a channel, so without this gate a member could forward
content into a read-only server channel they cannot post to.

These tests pin the fail-closed gate on the /forward route: denied send -> the
service is never invoked (no forward record / message), granted send -> the
forward proceeds, and a missing permission API or a failing lookup denies.

Run with: pytest src/tests/api/test_forward_send_permission_gate.py -q -n 0
"""

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.features.forwarding as route

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

VIEWER = 5
MESSAGE_ID = 20
TARGET_CONV = 99
TARGET_CHANNEL = 777
SERVER_ID = 1


class _User:
    token_type = "user"
    user_id = VIEWER


class _Servers:
    """Servers facade exposing the channel lookup and permission decision."""

    def __init__(self, allow_send, *, backing=True, lookup_error=False):
        self.allow_send = allow_send
        self.backing = backing
        self.lookup_error = lookup_error
        self.calls = []

    def get_channel_by_conversation(self, conversation_id):
        if self.lookup_error:
            raise RuntimeError("lookup exploded")
        if not self.backing or conversation_id != TARGET_CONV:
            return None
        return {"id": TARGET_CHANNEL, "server_id": SERVER_ID}

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.calls.append((user_id, server_id, permission, channel_id))
        return self.allow_send


class _Service:
    """Records whether the forwarding service was actually invoked."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.forward_calls = []
        type(self).instances.append(self)

    def forward_message(self, user_id, message_id, target_conversation_id):
        self.forward_calls.append((user_id, message_id, target_conversation_id))
        return {"new_message_id": 123, "forwarded_by": VIEWER}


@pytest.fixture(autouse=True)
def _patch_route(monkeypatch):
    _Service.instances = []
    monkeypatch.setattr(route, "ForwardingService", _Service)
    monkeypatch.setattr(route, "resolve_participant_service", lambda m: object())
    monkeypatch.setattr(api, "get_db", lambda: object())
    monkeypatch.setattr(api, "get_messaging", lambda: object())


async def _call():
    return await route.forward_message(
        route.ForwardMessageRequest(
            message_id=str(MESSAGE_ID),
            target_conversation_id=str(TARGET_CONV),
        ),
        current_user=_User(),
    )


class TestForwardSendGate:
    @pytest.mark.asyncio
    async def test_view_but_no_send_denies_and_never_forwards(self, monkeypatch):
        servers = _Servers(allow_send=False)
        monkeypatch.setattr(api, "get_servers", lambda: servers)

        with pytest.raises(HTTPException) as exc:
            await _call()

        assert exc.value.status_code == 403
        assert _Service.instances == []  # service never even constructed

    @pytest.mark.asyncio
    async def test_send_granted_proceeds(self, monkeypatch):
        servers = _Servers(allow_send=True)
        monkeypatch.setattr(api, "get_servers", lambda: servers)

        result = await _call()

        assert result["success"] is True
        assert _Service.instances[0].forward_calls == [
            (VIEWER, MESSAGE_ID, TARGET_CONV)
        ]
        assert (VIEWER, SERVER_ID, "messages.send", TARGET_CHANNEL) in servers.calls

    @pytest.mark.asyncio
    async def test_dm_target_skips_channel_gate(self, monkeypatch):
        servers = _Servers(allow_send=False, backing=False)
        monkeypatch.setattr(api, "get_servers", lambda: servers)

        result = await _call()

        assert result["success"] is True
        assert servers.calls == []  # no per-channel permission decision for DMs
        assert _Service.instances[0].forward_calls

    @pytest.mark.asyncio
    async def test_missing_permission_api_fails_closed(self, monkeypatch):
        class _NoPerm(_Servers):
            has_permission = None

        servers = _NoPerm(allow_send=True)
        monkeypatch.setattr(api, "get_servers", lambda: servers)

        with pytest.raises(HTTPException) as exc:
            await _call()

        assert exc.value.status_code == 403
        assert _Service.instances == []

    @pytest.mark.asyncio
    async def test_lookup_failure_fails_closed(self, monkeypatch):
        servers = _Servers(allow_send=True, lookup_error=True)
        monkeypatch.setattr(api, "get_servers", lambda: servers)

        with pytest.raises(HTTPException) as exc:
            await _call()

        assert exc.value.status_code == 403
        assert _Service.instances == []
