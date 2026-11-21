"""SECURITY: voice-message sends must pass the live channel send gate.

``send_voice_message`` publishes through ``messaging.send_message``, which does
not apply the REST send route's ``messages.send``, timeout or channel-view
gates. Conversation participation is seeded for every server member, so a
member who may only view a read-only (or timed-out) channel could otherwise
post a voice message into it. These tests pin the fail-closed gate on both
voice routes and on the shared helper.

Run with: pytest src/tests/api/test_voice_send_permission_gate.py -q -n 0
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.features.voice as voice
import src.core.messaging.services.voice as voice_service_module

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

USER_ID = 7
CONV_ID = 4242
CHANNEL_ID = 100
SERVER_ID = 200


class _Servers:
    def __init__(
        self,
        *,
        allow_send=True,
        timed_out=False,
        view=True,
        linked=True,
        lookup_error=False,
        perm_error=False,
    ):
        self.allow_send = allow_send
        self.timed_out = timed_out
        self.view = view
        self.linked = linked
        self.lookup_error = lookup_error
        self.perm_error = perm_error
        self.calls = []

    def get_channel_by_conversation(self, conversation_id):
        self.calls.append(("lookup", conversation_id))
        if self.lookup_error:
            raise RuntimeError("lookup down")
        if not self.linked:
            return None
        return {"id": CHANNEL_ID, "server_id": SERVER_ID}

    def get_channel(self, channel_id, user_id):
        self.calls.append(("get_channel", channel_id, user_id))
        return object() if self.view else None

    def is_timed_out(self, user_id, server_id):
        self.calls.append(("is_timed_out", user_id, server_id))
        return self.timed_out

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.calls.append(
            ("has_permission", user_id, server_id, permission, channel_id)
        )
        if self.perm_error:
            raise RuntimeError("permission service down")
        return self.allow_send


class _NoPermissionAPI(_Servers):
    has_permission = None


class _NoTimeoutAPI(_Servers):
    is_timed_out = None


class _NoViewAPI(_Servers):
    get_channel = None


def _assert_gate(servers):
    voice._require_voice_send_access(servers, USER_ID, CONV_ID)


def test_denies_when_messages_send_missing():
    servers = _Servers(allow_send=False)
    with pytest.raises(PermissionError):
        _assert_gate(servers)
    assert (
        "has_permission",
        USER_ID,
        SERVER_ID,
        "messages.send",
        CHANNEL_ID,
    ) in servers.calls


def test_denies_when_timed_out():
    servers = _Servers(timed_out=True)
    with pytest.raises(PermissionError):
        _assert_gate(servers)
    assert ("is_timed_out", USER_ID, SERVER_ID) in servers.calls


def test_denies_when_channel_not_viewable():
    with pytest.raises(PermissionError):
        _assert_gate(_Servers(view=False))


def test_denies_when_permission_api_missing():
    with pytest.raises(PermissionError):
        _assert_gate(_NoPermissionAPI())


def test_denies_when_timeout_api_missing():
    with pytest.raises(PermissionError):
        _assert_gate(_NoTimeoutAPI())


def test_denies_when_view_api_missing():
    with pytest.raises(PermissionError):
        _assert_gate(_NoViewAPI())


def test_denies_on_lookup_or_permission_error():
    with pytest.raises(PermissionError):
        _assert_gate(_Servers(lookup_error=True))
    with pytest.raises(PermissionError):
        _assert_gate(_Servers(perm_error=True))


def test_allows_authorized_channel_and_dm():
    servers = _Servers()
    _assert_gate(servers)  # no exception
    assert (
        "has_permission",
        USER_ID,
        SERVER_ID,
        "messages.send",
        CHANNEL_ID,
    ) in servers.calls

    # A conversation with no backing server channel is a DM/group; the
    # participation gate in VoiceMessageService covers it.
    _assert_gate(_Servers(linked=False, allow_send=False))


class _Service:
    instances = []

    def __init__(self, db):
        self.calls = []
        type(self).instances.append(self)

    def create_voice_message(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "id": 1,
            "conversation_id": kwargs["conversation_id"],
            "attachment_data": {"filename": "a.webm", "url": "/api/v1/media/a.webm"},
        }


class _Messaging:
    def __init__(self):
        self.sends = []

    def send_message(self, **kwargs):
        self.sends.append(kwargs)
        return SimpleNamespace(id=123)


def _body():
    return voice.VoiceMessageRequest(
        conversation_id=str(CONV_ID),
        duration_ms=1000,
        filename="note.webm",
        content_type="audio/webm",
        size=512,
        url="/api/v1/media/note.webm",
    )


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    _Service.instances = []
    messaging = _Messaging()
    monkeypatch.setattr(api, "get_db", lambda: object())
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(
        voice, "resolve_participant_service", lambda messaging: object()
    )
    monkeypatch.setattr(voice_service_module, "VoiceMessageService", _Service)
    return messaging


def _user():
    return SimpleNamespace(token_type="user", user_id=USER_ID)


@pytest.mark.asyncio
async def test_route_denies_send_before_any_voice_message(monkeypatch, _patch):
    servers = _Servers(allow_send=False)
    monkeypatch.setattr(api, "get_servers", lambda: servers)

    with pytest.raises(HTTPException) as exc:
        await voice.send_voice_message(_body(), current_user=_user())

    assert exc.value.status_code == 403
    assert _Service.instances == []
    assert _patch.sends == []


@pytest.mark.asyncio
async def test_route_allows_authorized_voice_send(monkeypatch, _patch):
    servers = _Servers(allow_send=True)
    monkeypatch.setattr(api, "get_servers", lambda: servers)

    result = await voice.send_voice_message(_body(), current_user=_user())

    assert result["success"] is True
    assert _Service.instances[0].calls[0]["conversation_id"] == CONV_ID
    assert _patch.sends
    assert _patch.sends[0]["conversation_id"] == CONV_ID
