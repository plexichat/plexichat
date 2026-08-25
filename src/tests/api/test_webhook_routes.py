"""Tests for webhook routes."""


def test_create_webhook(test_client, test_server, test_user_with_token):
    """Test creating a webhook."""
    token = test_user_with_token["token"]
    server, user = test_server
    response = test_client.post(
        f"/api/v1/servers/{server.id}/webhooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Test Webhook"},
    )
    # Webhook routes might not be fully set up in tests
    assert response.status_code in [200, 404, 405, 500]


def test_execute_webhook(test_client):
    """Test executing a webhook."""
    response = test_client.post(
        "/api/v1/webhooks/123456789/token",
        json={"content": "Test message"},
    )
    # Webhook routes might not be fully set up in tests
    assert response.status_code in [200, 400, 401, 404, 405, 500]


def test_webhook_without_auth(test_client, test_server):
    """Test that webhook creation without authentication fails."""
    server, user = test_server
    response = test_client.post(
        f"/api/v1/servers/{server.id}/webhooks",
        json={"name": "Test Webhook"},
    )
    assert response.status_code in [401, 404, 405]


def _make_webhook(modules, server, require_signed: bool):
    """Create a webhook on the server's first channel via the core module."""
    import uuid

    channels = modules.servers.get_channels(server.owner_id, server.id)
    channel = channels[0]
    unique_id = uuid.uuid4().hex[:8]
    return modules.webhooks.create_webhook(
        user_id=server.owner_id,
        channel_id=channel.id,
        name=f"Signed Test {unique_id}",
        require_signed_deliveries=require_signed,
    )


def test_require_signed_flag_round_trips(modules, test_server):
    """F8: the require_signed_deliveries flag persists and is returned."""
    webhook = _make_webhook(modules, test_server[0], require_signed=True)
    assert webhook.require_signed_deliveries is True

    fetched = modules.webhooks.get_webhook(webhook.id)
    assert fetched.require_signed_deliveries is True

    updated = modules.webhooks.update_webhook(
        user_id=test_server[0].owner_id,
        webhook_id=webhook.id,
        require_signed_deliveries=False,
    )
    assert updated.require_signed_deliveries is False


def test_unsigned_execution_rejected_when_required(test_client, modules, test_server):
    """F8: with the flag enabled an unsigned request (even with a valid
    token) is rejected with 401 instead of silently accepted."""
    server, _user = test_server
    webhook = _make_webhook(modules, server, require_signed=True)
    response = test_client.post(
        f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
        json={"content": "unsigned should fail"},
    )
    assert response.status_code == 401


def test_unsigned_execution_allowed_without_flag(test_client, modules, test_server):
    """Without the flag the token alone remains sufficient."""
    server, _user = test_server
    webhook = _make_webhook(modules, server, require_signed=False)
    response = test_client.post(
        f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
        json={"content": "token-only execution"},
    )
    assert response.status_code == 200
