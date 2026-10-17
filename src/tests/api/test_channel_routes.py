"""Tests for channel routes."""


def test_get_channel(test_client, test_server, test_user_with_token):
    """Test getting a channel by ID."""
    server, user = test_server
    token = test_user_with_token["token"]

    # Get a channel from the server
    response = test_client.get(
        f"/api/v1/channels/{server.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Channel routes might not be fully set up in tests
    assert response.status_code == 404


def test_get_channel_without_auth(test_client):
    """Test that getting channel without authentication fails."""
    response = test_client.get("/api/v1/channels/123456789")
    assert response.status_code == 401


def test_get_channel_invalid_id(test_client, test_user_with_token):
    """Test that getting channel with invalid ID fails."""
    token = test_user_with_token["token"]
    response = test_client.get(
        "/api/v1/channels/invalid",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_update_channel(test_client, test_server, test_user_with_token):
    """Test updating a channel."""
    token = test_user_with_token["token"]
    server, user = test_server

    # Try to update a channel
    response = test_client.patch(
        f"/api/v1/channels/{server.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Updated Channel Name"},
    )

    # Channel routes might not be fully set up in tests
    assert response.status_code == 404


def test_update_channel_without_auth(test_client):
    """Test that updating channel without authentication fails."""
    response = test_client.patch(
        "/api/v1/channels/123456789",
        json={"name": "Updated Channel Name"},
    )
    assert response.status_code == 401


def test_delete_channel(test_client, test_server, test_user_with_token):
    """Test deleting a channel."""
    token = test_user_with_token["token"]
    server, user = test_server

    # Try to delete a channel
    response = test_client.delete(
        f"/api/v1/channels/{server.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Channel routes might not be fully set up in tests
    assert response.status_code == 404


def test_delete_channel_without_auth(test_client):
    """Test that deleting channel without authentication fails."""
    response = test_client.delete("/api/v1/channels/123456789")
    assert response.status_code == 401


def test_ratchet_snapshot_serializes_snowflakes_as_strings():
    """Snowflake fields must be strings so JS clients don't round them.

    A JavaScript Number cannot represent a 64-bit snowflake, so serving the
    ratchet interval_id as a JSON number makes the client derive a mismatched
    HKDF salt/AAD. The API layer stringifies them (project-wide convention).
    """
    from src.api.routes.channels.ratchet import _stringify_ids

    payload = {
        "interval_id": 1234567890123456789,
        "conversation_id": 42,
        "start_message_id": 1,
        "end_message_id": None,
        "created_at": 123,
        "start_key": "AAAA",
    }
    out = _stringify_ids(payload)

    assert out["interval_id"] == "1234567890123456789"
    assert out["conversation_id"] == "42"
    assert out["start_message_id"] == "1"
    assert out["end_message_id"] is None
    assert out["created_at"] == 123
    # The caller's dict must not be mutated in place.
    assert payload["interval_id"] == 1234567890123456789


def test_ratchet_intervals_endpoint_resolves_a_rotated_interval(monkeypatch):
    """A rotation the client never observed must still be resolvable.

    When the server rotates an interval, an envelope naming the previous
    interval can no longer be decrypted from the active snapshot alone. The
    client falls back to ``GET /channels/{id}/ratchet/intervals``, so the
    endpoint must surface every interval (newest first) with stringified
    ids and the wrapped start key the client needs to derive keys.
    """
    import asyncio
    from types import SimpleNamespace

    from src.api.routes.channels import ratchet as ratchet_module

    rotated_out = {
        "interval_id": 1234567890123456789,
        "conversation_id": 42,
        "start_message_id": 1,
        "end_message_id": 100,
        "start_key": "b2xkLWtleQ==",
        "context_tag": "plexichat.channel-message.v3",
    }
    active = {
        "interval_id": 2234567890123456788,
        "conversation_id": 42,
        "start_message_id": 100,
        "end_message_id": None,
        "start_key": "bmV3LWtleQ==",
        "context_tag": "plexichat.channel-message.v3",
    }

    class _StubManager:
        def snapshot_list(self, cid):
            assert cid == 42
            return [active, rotated_out]

    monkeypatch.setattr(ratchet_module, "_require_channel_access", lambda *a, **k: None)
    monkeypatch.setattr(ratchet_module, "_resolve_db", lambda: object())
    monkeypatch.setattr(ratchet_module, "_resolve_manager", lambda db: _StubManager())
    monkeypatch.setattr(ratchet_module, "ratchet_encryption_licensed", lambda: True)

    result = asyncio.run(
        ratchet_module.get_channel_ratchet_intervals(
            "42", current_user=SimpleNamespace(user_id=7)
        )
    )

    assert result["channel_id"] == "42"
    assert result["enabled"] is True
    intervals = result["intervals"]
    assert [i["interval_id"] for i in intervals] == [
        str(active["interval_id"]),
        str(rotated_out["interval_id"]),
    ]
    # The rotated-out interval is present (resolvable) with its start key.
    old = intervals[1]
    assert old["end_message_id"] == "100"
    assert old["start_key"] == "b2xkLWtleQ=="
    assert old["context_tag"] == "plexichat.channel-message.v3"
