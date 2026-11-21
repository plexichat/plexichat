"""SECURITY: attachment uploads must not accept a server channel's
conversation-id alias to bypass the live channel send gate.

The upload route also accepts a channel's linked ``conversation_id`` as a URL
alias (the ``messaging.get_conversation`` fallback). Conversation participation
is seeded for every server member on join and is NOT a send grant, so a member
who can only view (or is timed out on, or lacks ``messages.send`` in) a channel
could otherwise upload an attachment "to" it by putting the linked conversation
id in the URL. The route must reverse-resolve the alias membership-agnostically
and require a live view + send grant on the real backing channel before the
body is stored.

Run with: pytest src/tests/api/test_attachment_upload_conversation_alias_send_gate.py -q -n 0
"""

import io
from typing import cast

import pytest
from fastapi import HTTPException, UploadFile

import src.api as api
from src.api.routes.channels import attachments as route
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER_ID = 7
ALIAS_CONV_ID = 4242  # the linked conversation id used as the URL alias
REAL_CHANNEL_ID = 100
SERVER_ID = 200


def _token():
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=USER_ID,
        user_id=USER_ID,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="member",
        account_type=AccountType.USER,
    )


class _Conversation:
    id = ALIAS_CONV_ID


class _Servers:
    """Membership gate satisfied for the alias; only the send gate can deny."""

    def __init__(
        self,
        *,
        view=True,
        allow_send=True,
        timed_out=False,
        linked=True,
        lookup_error=False,
        perm_error=False,
    ):
        self.view = view
        self.allow_send = allow_send
        self.timed_out = timed_out
        self.linked = linked
        self.lookup_error = lookup_error
        self.perm_error = perm_error
        self.calls = []

    def get_channel(self, channel_id, user_id):
        self.calls.append(("get_channel", int(channel_id), user_id))
        # The alias is a conversation id, never a srv_channels row id.
        if int(channel_id) == REAL_CHANNEL_ID:
            return object() if self.view else None
        return None

    def get_conversation(self, conversation_id, user_id):
        self.calls.append(("get_conversation", int(conversation_id), user_id))
        if int(conversation_id) == ALIAS_CONV_ID:
            return _Conversation()
        return None

    def get_channel_by_conversation(self, conversation_id):
        self.calls.append(("lookup", int(conversation_id)))
        if self.lookup_error:
            raise RuntimeError("lookup down")
        if not self.linked:
            return None
        return {"id": REAL_CHANNEL_ID, "server_id": SERVER_ID}

    def channel_exists(self, channel_id):
        return False  # the alias is not a channel id

    def conversation_exists(self, conversation_id):
        return int(conversation_id) == ALIAS_CONV_ID

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


class _UploadResult:
    file_id = 9
    filename = "a.png"
    size = 10
    content_type = "image/png"
    url = "/api/v1/media/a.png"
    checksum = "abc123"
    thumbnails = None


class _Media:
    def __init__(self):
        self.calls = []

    def upload_stream(self, **kwargs):
        self.calls.append(kwargs)
        return _UploadResult()


class _FakeUploadFile:
    """Small, validly-sized multipart stand-in (passes the tier cap check)."""

    def __init__(self):
        self.size = 10
        self.filename = "a.png"
        self.content_type = "image/png"
        self.file = io.BytesIO(b"x" * 10)


def _wire(monkeypatch, servers, media):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    # The messaging store exposes get_conversation/conversation_exists; the
    # fake servers object does too, so reuse it for both probes.
    monkeypatch.setattr(api, "get_messaging", lambda: servers)
    monkeypatch.setattr(api, "get_media", lambda: media)
    monkeypatch.setattr(api, "get_db", lambda: None)


@pytest.fixture
def env(monkeypatch):
    servers = _Servers()
    media = _Media()
    _wire(monkeypatch, servers, media)
    return {"servers": servers, "media": media}


async def _upload(alias=str(ALIAS_CONV_ID)):
    return await route.ChannelAttachmentsMixin()._upload_attachment(
        alias, cast(UploadFile, _FakeUploadFile()), current_user=_token()
    )


@pytest.mark.asyncio
async def test_alias_upload_denied_when_send_permission_missing(env, monkeypatch):
    """Read-only channel member cannot upload through the conversation alias."""
    servers = _Servers(view=True, allow_send=False)
    _wire(monkeypatch, servers, env["media"])
    with pytest.raises(HTTPException) as exc:
        await _upload()
    assert exc.value.status_code == 403
    assert env["media"].calls == []
    assert (
        "has_permission",
        USER_ID,
        SERVER_ID,
        "messages.send",
        REAL_CHANNEL_ID,
    ) in servers.calls


@pytest.mark.asyncio
async def test_alias_upload_denied_when_channel_not_viewable(env, monkeypatch):
    servers = _Servers(view=False, allow_send=True)
    _wire(monkeypatch, servers, env["media"])
    with pytest.raises(HTTPException) as exc:
        await _upload()
    assert exc.value.status_code == 403
    assert env["media"].calls == []


@pytest.mark.asyncio
async def test_alias_upload_denied_when_timed_out(env, monkeypatch):
    servers = _Servers(view=True, allow_send=True, timed_out=True)
    _wire(monkeypatch, servers, env["media"])
    with pytest.raises(HTTPException) as exc:
        await _upload()
    assert exc.value.status_code == 403
    assert env["media"].calls == []
    assert ("is_timed_out", USER_ID, SERVER_ID) in servers.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory",
    [
        lambda: _Servers(lookup_error=True),
        lambda: _Servers(perm_error=True),
        lambda: _NoPermissionAPI(),
        lambda: _NoTimeoutAPI(),
        lambda: _NoViewAPI(),
    ],
)
async def test_alias_upload_fails_closed_on_unresolved_permissions(
    env, monkeypatch, factory
):
    servers = factory()
    _wire(monkeypatch, servers, env["media"])
    with pytest.raises(HTTPException) as exc:
        await _upload()
    assert exc.value.status_code == 403
    assert env["media"].calls == []


@pytest.mark.asyncio
async def test_authorized_member_can_upload_through_alias(env):
    result = await _upload()
    assert result.id == "9"
    assert len(env["media"].calls) == 1
    assert env["media"].calls[0]["user_id"] == USER_ID


@pytest.mark.asyncio
async def test_dm_conversation_does_not_require_channel_send(env, monkeypatch):
    """A conversation with no backing server channel is a genuine DM/group."""
    servers = _Servers(linked=False, allow_send=False)
    _wire(monkeypatch, servers, env["media"])
    result = await _upload()
    assert result.id == "9"
    assert len(env["media"].calls) == 1


# --- helper-level fail-closed checks (mirror the voice send-gate tests) ---


def test_helper_denies_missing_send():
    with pytest.raises(PermissionError):
        route._require_attachment_send_access(
            _Servers(allow_send=False), USER_ID, ALIAS_CONV_ID
        )


def test_helper_denies_missing_view():
    with pytest.raises(PermissionError):
        route._require_attachment_send_access(
            _Servers(view=False), USER_ID, ALIAS_CONV_ID
        )


def test_helper_allows_authorized_and_dm():
    route._require_attachment_send_access(_Servers(), USER_ID, ALIAS_CONV_ID)
    route._require_attachment_send_access(
        _Servers(linked=False, allow_send=False), USER_ID, ALIAS_CONV_ID
    )
