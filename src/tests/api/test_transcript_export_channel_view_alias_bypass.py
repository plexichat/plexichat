"""Transcript export must not be a conversation-id alias authz bypass.

``GET/POST /channels/{channel_id}/messages/export`` first resolves the URL id
with ``servers.get_channel``. That accessor returns ``None`` both for "not a
server channel" and for a channel the caller cannot ``channels.view``. When it
is ``None`` the route falls back to authorizing the id *directly* against
messaging data: it looks up ``msg_conversations`` by the URL id and accepts any
mere participant of that conversation.

For a server channel the URL id may be the channel's linked *conversation* id.
Addressing a private channel by that alias makes ``get_channel`` return
``None``, so the participant-only fallback runs: every server member is seeded
as a participant of the channel's conversation on join, independent of
``channels.view``. A member denied view can therefore export the full transcript
of a channel they cannot see (and the ``messages.export_transcript`` check,
which only lives in the resolved-server-channel branch, is skipped entirely).

These regressions use a real server manager with a per-member ``channels.view``
deny override and assert that the private transcript is not exported.
"""

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.export as export_route
import src.core.messaging.services.export as export_service
from src.api.middleware.authentication import TokenInfo
from src.core.auth.models import AccountType
from src.api.schemas.messages import TranscriptExportRequest


pytestmark = [pytest.mark.unit, pytest.mark.security]


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


class _NoCloseDB:
    """Delegate reads to the shared test db but make close() a no-op."""

    def __init__(self, db):
        self._db = db

    def fetch_one(self, sql, params=()):
        return self._db.fetch_one(sql, params)

    def fetch_all(self, sql, params=()):
        return self._db.fetch_all(sql, params)

    def execute(self, sql, params=()):
        return self._db.execute(sql, params)

    def close(self):
        pass


@pytest.fixture
def restricted_export_env(
    db, messaging_manager, server_manager, two_users, monkeypatch
):
    owner, member = two_users
    server = server_manager.create_server(owner.id, f"xport-{id(server_manager)}")
    channel = server_manager.create_channel(owner.id, server.id, "secret", "text")
    # Joining after the channel exists seeds the member as a participant of
    # the channel's conversation -- exactly what a real server member gets,
    # independent of channels.view.
    server_manager.add_member(server.id, member.id)
    server_manager.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=member.id,
        deny={"channels.view": True},
    )
    assert server_manager.get_channel(channel.id, member.id) is None
    assert server_manager.get_channel(channel.id, owner.id) is not None
    messaging_manager.send_message(owner.id, channel.conversation_id, "private content")

    captured = []

    def _fake_request_export(self, uid, conv_id, fmt, frm, to, tz):
        captured.append((uid, conv_id))
        return {
            "export_id": "export_probe",
            "status": "pending",
            "message_count": 1,
            "file_url": None,
            "expires_at": None,
            "error": None,
        }

    monkeypatch.setattr(api, "_servers", server_manager)
    monkeypatch.setattr(api, "_messaging", messaging_manager)
    monkeypatch.setattr(api, "get_db", lambda: _NoCloseDB(db))
    monkeypatch.setattr(
        export_service.TranscriptExportService,
        "request_export",
        _fake_request_export,
    )
    return {
        "owner": owner,
        "member": member,
        "channel": channel,
        "captured": captured,
    }


async def _export(uid, name, channel_id):
    return await export_route.request_transcript_export(
        str(channel_id),
        TranscriptExportRequest(),
        _token(uid, name),
    )


@pytest.mark.asyncio
async def test_member_denied_view_cannot_export_via_conversation_id_alias(
    restricted_export_env,
):
    env = restricted_export_env
    with pytest.raises(HTTPException) as exc:
        await _export(env["member"].id, "m", env["channel"].conversation_id)
    assert exc.value.status_code in (403, 404)
    assert env["captured"] == [], "the private transcript must not be exported"


@pytest.mark.asyncio
async def test_owner_can_still_export_via_conversation_id_alias(restricted_export_env):
    env = restricted_export_env
    result = await _export(env["owner"].id, "owner", env["channel"].conversation_id)
    assert result.export_id == "export_probe"
    assert env["captured"] == [(env["owner"].id, env["channel"].conversation_id)]
