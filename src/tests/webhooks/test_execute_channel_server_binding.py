"""Webhook execution must bind its delivery channel to the webhook's own server.

The ``thread_id`` target is checked against the webhook's server, but the
webhook's *primary* delivery channel (``webhook_webhooks.channel_id``) drives
the conversation the message is written to. If that row resolves to a channel
on a different server, a token holder posts into a foreign guild. This test
proves a mismatched record is rejected instead of delivering cross-tenant.
"""

import uuid

import pytest

from src.core.webhooks.exceptions import InvalidWebhookContentError


def test_execute_rejects_channel_on_foreign_server(webhook_with_token):
    setup = webhook_with_token
    db = setup["db"]

    other_owner = setup["auth"].register(
        username=f"foreign_owner_{uuid.uuid4().hex[:8]}",
        email=f"foreign_owner_{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass123!",
    )
    foreign_server = setup["servers"].create_server(other_owner.id, "Foreign Guild")
    foreign_channels = setup["servers"].get_channels(other_owner.id, foreign_server.id)
    assert foreign_channels, "expected the foreign guild to have a default channel"
    foreign_channel_id = foreign_channels[0].id

    # Simulate a stale/partially-migrated webhook record: channel_id now points
    # at the foreign guild's channel while server_id still names the original
    # guild. A token holder must not be able to post into that foreign channel.
    db.execute(
        "UPDATE webhook_webhooks SET channel_id = ? WHERE id = ?",
        (foreign_channel_id, setup["webhook"].id),
    )

    with pytest.raises(InvalidWebhookContentError):
        setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="cross-guild delivery",
            wait=True,
        )


def test_execute_allows_channel_on_own_server(webhook_with_token):
    """Control: a webhook whose channel matches its server still delivers."""
    setup = webhook_with_token

    result = setup["webhooks"].execute_webhook(
        webhook_id=setup["webhook"].id,
        token=setup["token"],
        content="same-guild delivery",
        wait=True,
    )
    assert result is not None
    assert result.content == "same-guild delivery"
