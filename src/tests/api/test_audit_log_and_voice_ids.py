"""Audit-log and voice-info ids must cross the wire as strings.

``GET /users/@me/audit-logs`` returned raw DB integers while the canonical
``AuditLogEntryResponse`` declares ``SnowflakeID`` (string) fields, and
``GET /voice/channels/{id}/info`` declared ``channel_id: int`` while every
other channel/server schema uses ``SnowflakeID``. A JS ``Number`` cannot
hold a 64-bit snowflake exactly, so a numeric id would be rounded by the
client and subsequent lookups would miss.
"""

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_user_audit_logs_stringifies_ids(monkeypatch):
    """The self audit-log endpoint must emit id fields as decimal strings."""
    import src.api.routes.features.audit_logs as audit_logs_module

    big = 9007199254740993  # past 2^53: a JSON number would lose precision

    class _FakeServers:
        def get_servers(self, user_id):
            return [{"id": big}]

        def get_permissions(self, user_id, server_id):
            return {"server.view_audit_log": True}

    class _FakeDB:
        def fetch_all(self, query, params=None):
            return [
                {
                    "id": big,
                    "server_id": big,
                    "user_id": big,
                    "action": "member.ban",
                    "target_type": "user",
                    "target_id": big,
                    "changes": None,
                    "reason": None,
                    "created_at": 1700000000000,
                },
                (7, big, big, "member.kick", "user", None, None, None, 1700000000001),
            ]

    monkeypatch.setattr(audit_logs_module.api, "get_servers", lambda: _FakeServers())
    monkeypatch.setattr(audit_logs_module.api, "get_db", lambda: _FakeDB())

    result = await audit_logs_module.get_user_audit_logs(
        server_id=None,
        action=None,
        limit=25,
        current_user=SimpleNamespace(user_id=big),
    )

    assert len(result["entries"]) == 2
    first = result["entries"][0]
    assert first["id"] == str(big)
    assert isinstance(first["id"], str)
    assert first["server_id"] == str(big)
    assert isinstance(first["server_id"], str)
    assert first["user_id"] == str(big)
    assert isinstance(first["user_id"], str)
    assert first["target_id"] == str(big)
    assert isinstance(first["target_id"], str)
    # Non-id fields keep their native shapes.
    assert first["action"] == "member.ban"
    assert first["created_at"] == 1700000000000

    # Tuple rows (SELECT *) take the same string-id shape, and a NULL
    # target_id stays None instead of becoming "None".
    second = result["entries"][1]
    assert second["id"] == "7"
    assert isinstance(second["id"], str)
    assert second["target_id"] is None


def test_voice_channel_info_response_stringifies_channel_id():
    """The voice info schema must serialize channel_id as a string."""
    from src.api.schemas.voice import VoiceChannelInfoResponse

    big = 9007199254740993
    model = VoiceChannelInfoResponse(
        channel_id=big, session_id="sess", ice_servers=[], bitrate=64000
    )

    assert model.channel_id == str(big)
    body = model.model_dump(mode="json")
    assert body["channel_id"] == str(big)
    assert isinstance(body["channel_id"], str)
    assert body["bitrate"] == 64000
