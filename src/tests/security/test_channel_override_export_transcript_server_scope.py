"""A per-channel override must not grant server-scoped transcript export.

``messages.export_transcript`` is a SERVER-scoped permission: in the authoritative
``SERVER_PERMISSIONS`` catalog it lives under its own ``# Transcript export``
section, NOT under the ``# Message permissions (per channel)`` section. Granting
it through a role is therefore a server-wide decision.

``request_transcript_export`` used to resolve the gate with

    servers_mod.get_permissions(user_id, server_id, cid)

i.e. WITH the target channel id. That folds the channel's per-role/per-member
overrides into the result, so a member with no server-level grant could export a
full transcript purely because a single channel override allowed
``messages.export_transcript``. Unlike ``has_permission``/``require_permission``
checks, this dict-based ``get_permissions(...)[perm]`` form is invisible to the
scope-mismatch static guard, which is why a dedicated regression is needed.

The tests below prove the grant is real at channel scope and absent at server
scope (so they are not vacuous), then require the endpoint to DENY it. Before
the fix the denial test fails because the export actually goes through.
"""

import json
import uuid

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.export as export_route
import src.core.messaging.services.export as export_service
from src.api.middleware.authentication import TokenInfo
from src.api.schemas.messages import TranscriptExportRequest
from src.core.auth.models import AccountType

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
def export_scope_env(db, messaging_manager, server_manager, two_users, monkeypatch):
    owner, member = two_users
    server = server_manager.create_server(owner.id, f"xscope-{uuid.uuid4().hex}")
    channel = server_manager.create_channel(owner.id, server.id, "general", "text")
    server_manager.add_member(server.id, member.id)

    # The member can see the channel (default @everyone grant), so the endpoint
    # reaches the permission gate rather than failing earlier on view access.
    assert server_manager.get_channel(channel.id, member.id) is not None

    # ``messages.export_transcript`` is in DEFAULT_EVERYONE_PERMISSIONS, so a
    # stock member already holds it server-wide. Revoke it from @everyone to
    # model a server that deliberately disabled export; the owner (who keeps
    # every permission) can still export and can still set overrides.
    everyone = next(
        r for r in server_manager.get_roles(owner.id, server.id) if r.is_default
    )
    everyone_perms = everyone.permissions
    if isinstance(everyone_perms, str):
        everyone_perms = json.loads(everyone_perms)
    everyone_perms = dict(everyone_perms)
    everyone_perms.pop("messages.export_transcript", None)
    server_manager.update_role(owner.id, everyone.id, permissions=everyone_perms)

    # NEGATIVE PRECONDITION: no server-level grant of the server-scoped bit.
    assert not server_manager.get_permissions(member.id, server.id).get(
        "messages.export_transcript"
    )

    # The only grant is a per-channel override on this one channel.
    server_manager.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=member.id,
        allow={"messages.export_transcript": True},
    )
    # NON-VACUITY: the override really grants the bit at channel scope, so the
    # endpoint's old channel-scoped lookup would have authorised the export.
    assert (
        server_manager.get_permissions(member.id, server.id, channel.id).get(
            "messages.export_transcript"
        )
        is True
    )
    assert not server_manager.get_permissions(member.id, server.id).get(
        "messages.export_transcript"
    ), "the grant must be channel-only, not a server-wide role grant"

    captured = []

    def _fake_request_export(self, uid, conv_id, fmt, frm, to, tz):
        captured.append((uid, conv_id))
        return {
            "export_id": "export_scope_probe",
            "status": "pending",
            "message_count": 0,
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
async def test_channel_override_cannot_authorize_server_scoped_export(export_scope_env):
    """A channel-only messages.export_transcript grant must not export anything."""
    env = export_scope_env
    with pytest.raises(HTTPException) as exc:
        await _export(env["member"].id, "m", env["channel"].id)
    assert exc.value.status_code == 403
    assert env["captured"] == [], "the transcript must not be created"


@pytest.mark.asyncio
async def test_server_scoped_grant_still_exports(export_scope_env):
    """Positive control: the owner's server-level grant still authorizes export."""
    env = export_scope_env
    result = await _export(env["owner"].id, "o", env["channel"].id)
    assert result.export_id == "export_scope_probe"
    assert env["captured"] == [(env["owner"].id, env["channel"].conversation_id)]
