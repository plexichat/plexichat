"""
Tests for webhook token validation and regeneration.
"""

import pytest


class TestTokenFormat:
    """Tests for webhook token format."""

    def test_token_format_structure(self, webhook_with_token):
        """Test token has correct structure."""
        setup = webhook_with_token
        token = setup["token"]

        parts = token.split(".")
        assert len(parts) == 3
        assert parts[0] == "webhook"
        assert parts[1] == str(setup["webhook"].id)
        assert len(parts[2]) > 20

    def test_token_secret_is_random(self, fresh_server):
        """Test that token secrets are random."""
        setup = fresh_server
        secrets = set()

        for i in range(10):
            webhook = setup["webhooks"].create_webhook(
                user_id=setup["owner"].id,
                channel_id=setup["channel"].id,
                name=f"Random Token {i}",
            )
            secret = webhook.token.split(".")[-1]
            secrets.add(secret)

        assert len(secrets) == 10

    def test_token_not_stored_in_plain(self, webhook_with_token):
        """Test that token is not stored in plain text."""
        setup = webhook_with_token
        token_secret = setup["token"].split(".")[-1]

        row = setup["db"].fetch_one(
            "SELECT token_hash FROM webhook_webhooks WHERE id = ?",
            (setup["webhook"].id,),
        )

        assert row is not None
        assert row["token_hash"] != token_secret
        assert len(row["token_hash"]) == 64


class TestTokenValidation:
    """Tests for token validation."""

    def test_valid_token_accepted(self, webhook_with_token):
        """Test that valid token is accepted."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Valid token",
            wait=True,
        )

        assert result is not None

    def test_invalid_token_rejected(self, webhook_with_token):
        """Test that invalid token is rejected."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token="webhook.123.invalid_secret",
                content="Invalid token",
            )

    def test_wrong_webhook_id_in_token(self, webhook_with_token):
        """Test token with wrong webhook ID."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        secret = setup["token"].split(".")[-1]

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=f"webhook.999999.{secret}",
                content="Wrong ID",
            )

    def test_malformed_token_rejected(self, webhook_with_token):
        """Test that malformed token is rejected."""
        setup = webhook_with_token
        from src.core.webhooks import InvalidWebhookTokenError

        malformed_tokens = [
            "not_a_token",
            "webhook.abc.secret",
            "webhook.123",
            "bot.123.secret",
            "",
            "...",
        ]

        for token in malformed_tokens:
            with pytest.raises(InvalidWebhookTokenError):
                setup["webhooks"].execute_webhook(
                    webhook_id=setup["webhook"].id, token=token, content="Malformed"
                )


class TestTokenRegeneration:
    """Tests for token regeneration."""

    def test_regenerate_token(self, webhook_with_token):
        """Test regenerating webhook token."""
        setup = webhook_with_token
        old_token = setup["token"]

        updated = setup["webhooks"].regenerate_token(
            user_id=setup["owner"].id, webhook_id=setup["webhook"].id
        )

        assert updated.token is not None
        assert updated.token != old_token

    def test_regenerate_token_returns_new_token(self, webhook_with_token):
        """Test that regenerate returns the new token."""
        setup = webhook_with_token

        updated = setup["webhooks"].regenerate_token(
            user_id=setup["owner"].id, webhook_id=setup["webhook"].id
        )

        assert updated.token.startswith("webhook.")
        assert str(setup["webhook"].id) in updated.token

    def test_old_token_invalid_after_regenerate(self, webhook_with_token):
        """Test that old token is invalid after regeneration."""
        setup = webhook_with_token
        old_token = setup["token"]
        from src.core.webhooks import InvalidWebhookTokenError

        setup["webhooks"].regenerate_token(
            user_id=setup["owner"].id, webhook_id=setup["webhook"].id
        )

        with pytest.raises(InvalidWebhookTokenError):
            setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id, token=old_token, content="Old token"
            )

    def test_new_token_valid_after_regenerate(self, webhook_with_token):
        """Test that new token works after regeneration."""
        setup = webhook_with_token

        updated = setup["webhooks"].regenerate_token(
            user_id=setup["owner"].id, webhook_id=setup["webhook"].id
        )

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=updated.token,
            content="New token works",
            wait=True,
        )

        assert result is not None

    def test_regenerate_updates_timestamp(self, webhook_with_token):
        """Test that regeneration updates timestamp."""
        setup = webhook_with_token
        import time

        time.sleep(0.01)

        updated = setup["webhooks"].regenerate_token(
            user_id=setup["owner"].id, webhook_id=setup["webhook"].id
        )

        assert updated.updated_at > setup["webhook"].created_at

    def test_regenerate_not_found(self, fresh_server):
        """Test regenerating token for non-existent webhook."""
        setup = fresh_server
        from src.core.webhooks import WebhookNotFoundError

        with pytest.raises(WebhookNotFoundError):
            setup["webhooks"].regenerate_token(
                user_id=setup["owner"].id, webhook_id=999999999
            )

    def test_regenerate_no_permission(self, webhook_with_token):
        """Test regenerating token without permission."""
        setup = webhook_with_token
        from src.core.webhooks import PermissionDeniedError

        with pytest.raises(PermissionDeniedError):
            setup["webhooks"].regenerate_token(
                user_id=setup["non_member"].id, webhook_id=setup["webhook"].id
            )

    def test_multiple_regenerations(self, webhook_with_token):
        """Test multiple token regenerations."""
        setup = webhook_with_token
        tokens = set()

        for _ in range(5):
            updated = setup["webhooks"].regenerate_token(
                user_id=setup["owner"].id, webhook_id=setup["webhook"].id
            )
            tokens.add(updated.token)

        assert len(tokens) == 5


class TestTokenSecretOnly:
    """Tests for using just the secret part of token."""

    def test_execute_with_secret_only(self, webhook_with_token):
        """Test execution with just the secret part."""
        setup = webhook_with_token
        secret = setup["token"].split(".")[-1]

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=secret,
            content="Secret only",
            wait=True,
        )

        assert result is not None

    def test_execute_with_full_token(self, webhook_with_token):
        """Test execution with full token."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Full token",
            wait=True,
        )

        assert result is not None
