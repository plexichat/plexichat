"""
Regression tests for webhook bugs fixed during the audit.

W1: update_webhook bound the wrong parameter when the avatar was cleared
    (data corruption / off-by-one param binding).
W2: Ed25519 signing was dead code; verify it is now produced on delivery
    and verified on inbound execution.
"""

import json


class TestUpdateWebhookAvatarClearBinding:
    """W1: clearing the avatar while changing another field must not corrupt data."""

    def test_clear_avatar_keeps_other_fields(self, base_server_setup):
        wh = base_server_setup["webhooks"]
        owner = base_server_setup["owner"]
        channel = base_server_setup["channel"]
        db = base_server_setup["db"]

        webhook = wh.create_webhook(
            user_id=owner.id,
            channel_id=channel.id,
            name="Original",
            avatar_url="https://example.com/a.png",
        )

        wh.update_webhook(
            user_id=owner.id,
            webhook_id=webhook.id,
            name="Renamed",
            avatar_url="",
            channel_id=None,
        )

        row = db.fetch_one(
            "SELECT name, avatar_url, channel_id FROM webhook_webhooks WHERE id = ?",
            (webhook.id,),
        )
        # The avatar must be cleared, the name updated, and the channel
        # untouched (it must NOT have been clobbered by the avatar value).
        assert row["name"] == "Renamed"
        assert row["avatar_url"] is None
        assert row["channel_id"] == channel.id

    def test_clear_avatar_without_other_change(self, base_server_setup):
        wh = base_server_setup["webhooks"]
        owner = base_server_setup["owner"]
        channel = base_server_setup["channel"]
        db = base_server_setup["db"]

        webhook = wh.create_webhook(
            user_id=owner.id,
            channel_id=channel.id,
            name="KeepName",
            avatar_url="https://example.com/a.png",
        )

        wh.update_webhook(
            user_id=owner.id,
            webhook_id=webhook.id,
            name=None,
            avatar_url="",
            channel_id=None,
        )

        row = db.fetch_one(
            "SELECT name, avatar_url FROM webhook_webhooks WHERE id = ?",
            (webhook.id,),
        )
        assert row["name"] == "KeepName"
        assert row["avatar_url"] is None


class TestWebhookSigningWired:
    """W2: signing must now be produced and verifiable."""

    def test_sign_payload_returns_headers_when_keys_present(self, base_server_setup):
        wh = base_server_setup["webhooks"]
        owner = base_server_setup["owner"]
        channel = base_server_setup["channel"]

        webhook = wh.create_webhook(
            user_id=owner.id, channel_id=channel.id, name="Signer"
        )
        headers = wh.sign_payload(webhook.id, {"event": "message.created"})
        assert headers.get("X-Plexichat-Signature")
        assert headers.get("X-Plexichat-Timestamp")

    def test_enqueue_signs_when_manager_wired(self, base_server_setup):
        from src.core.webhooks.retry import WebhookRetryQueue

        wh = base_server_setup["webhooks"]
        owner = base_server_setup["owner"]
        channel = base_server_setup["channel"]
        db = base_server_setup["db"]

        webhook = wh.create_webhook(
            user_id=owner.id, channel_id=channel.id, name="Deliverer"
        )
        queue = WebhookRetryQueue(db, webhook_manager=wh)
        record = queue.enqueue(
            webhook.id,
            "message.created",
            {"hello": "world"},
            "https://example.com/hook",
        )
        stored = db.fetch_one(
            "SELECT metadata FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        metadata = json.loads(stored["metadata"]) if stored["metadata"] else {}
        assert "original_signing_headers" in metadata
        assert metadata["original_signing_headers"].get("X-Plexichat-Signature")

    def test_verify_payload_round_trip(self, base_server_setup):
        wh = base_server_setup["webhooks"]
        owner = base_server_setup["owner"]
        channel = base_server_setup["channel"]

        webhook = wh.create_webhook(
            user_id=owner.id, channel_id=channel.id, name="Verifiable"
        )
        payload = {"content": "hi", "embeds": []}
        headers = wh.sign_payload(webhook.id, payload)
        assert wh.verify_payload(
            webhook.id,
            payload,
            headers["X-Plexichat-Signature"],
            headers["X-Plexichat-Timestamp"],
        )
        # Tampered payload must fail verification.
        assert not wh.verify_payload(
            webhook.id,
            {"content": "bye"},
            headers["X-Plexichat-Signature"],
            headers["X-Plexichat-Timestamp"],
        )
