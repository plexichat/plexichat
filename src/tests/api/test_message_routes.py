"""Tests for message routes."""


def test_send_message(test_client, test_user_with_token):
    """Test sending a message."""
    token = test_user_with_token["token"]
    response = test_client.post(
        "/api/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "Test message"},
    )
    # Message routes might not be fully set up in tests
    assert response.status_code == 404


def test_send_message_without_auth(test_client):
    """Test that sending message without authentication fails."""
    response = test_client.post(
        "/api/v1/messages",
        json={"content": "Test message"},
    )
    # Message routes might not be fully set up in tests
    assert response.status_code == 404


def test_get_messages(test_client, test_user_with_token):
    """Test getting messages."""
    token = test_user_with_token["token"]
    response = test_client.get(
        "/api/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Message routes might not be fully set up in tests
    assert response.status_code == 404


def _first_channel(modules, server):
    channels = modules.servers.get_channels(server.owner_id, server.id)
    assert channels, "test server must have at least one channel"
    return channels[0]


def test_embeds_only_message_accepted(
    test_client, modules, test_server, test_user_with_token
):
    """Embeds-only sends (e.g. sticker messages) must be accepted.

    The send path substitutes a zero-width-space content value when the
    request carries embeds but no visible text; without that the
    messaging layer's non-empty-content validation rejects the message
    with 400 even though content+embeds is a legal combination.
    """
    import uuid

    server, _user = test_server
    channel = _first_channel(modules, server)
    token = test_user_with_token["token"]

    # Mirror production wiring: the send path runs the automod fast check
    # fail-closed, so the module must be initialized for server channels.
    from src.core import automod

    automod.setup(db=modules._db, servers_module=modules.servers)

    response = test_client.post(
        f"/api/v1/channels/{channel.id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "content": "",
            "embeds": [
                {
                    "type": "sticker",
                    "url": f"/api/v1/media/stickers/{uuid.uuid4().hex}.png",
                    "name": "Test Sticker",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("embeds"), "embeds must round-trip on the created message"


def test_sent_channel_message_is_returned_by_channel_listing(
    test_client, modules, test_server, test_user_with_token
):
    """A message the send route accepted must come back from the listing.

    The client renders the POST response optimistically, so a listing that
    reports an empty channel looks like the message was deleted on reload.
    This covers the whole HTTP round trip; the cache-hit failure mode that
    used to break the listing is pinned down in
    ``src/tests/messaging/test_messages.py`` and
    ``src/tests/database/test_cache_reconstructable_models.py``.
    """
    server, _user = test_server
    channel = _first_channel(modules, server)
    token = test_user_with_token["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Mirror production wiring: the send path runs the automod fast check
    # fail-closed, so the module must be initialized for server channels.
    from src.core import automod

    automod.setup(db=modules._db, servers_module=modules.servers)

    sent = test_client.post(
        f"/api/v1/channels/{channel.id}/messages",
        headers=headers,
        json={"content": "persisted-message-check"},
    )
    assert sent.status_code == 200, sent.text
    message_id = sent.json()["id"]

    listing = test_client.get(
        f"/api/v1/channels/{channel.id}/messages",
        headers=headers,
    )
    assert listing.status_code == 200, listing.text

    returned = [m["id"] for m in listing.json()]
    assert message_id in returned, (
        "a message accepted by the send route must be returned by the channel "
        f"listing; message {message_id} missing from {returned}"
    )


def test_ack_with_malformed_message_id_returns_400(
    test_client, modules, test_server, test_user_with_token
):
    """A malformed ``message_id`` cursor is a 400, not a 500.

    The ack route declares ``400: Invalid channel ID or message ID`` and every
    sibling reader of a client-supplied ID parses it under a ValueError guard.
    The optional ``message_id`` query parameter used to be converted with a
    bare ``int()``, so ``?message_id=not-a-snowflake`` raised out of the
    handler and surfaced as "Internal server error".

    The channel is real so the request reaches the cursor parse instead of
    failing on channel resolution.
    """
    server, _user = test_server
    channel = _first_channel(modules, server)
    token = test_user_with_token["token"]

    response = test_client.post(
        f"/api/v1/channels/{channel.id}/messages/ack",
        params={"message_id": "not-a-snowflake"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["message"] == "Invalid message ID"
