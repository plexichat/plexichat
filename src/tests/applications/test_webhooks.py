"""Tests for application webhook signature verification."""

import pytest
import hmac
import hashlib
import time

from src.core.applications.exceptions import WebhookSignatureError


@pytest.mark.applications
class TestWebhooks:
    """Tests for webhook signature verification."""

    def test_verify_valid_signature(self, app_manager, test_user):
        """Test verifying a valid webhook signature."""
        app_manager.create_application(owner_id=test_user.id, name="Webhook App")
        body = b'{"type": 1}'
        timestamp = str(int(time.time()))
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = timestamp.encode() + body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        result = app_manager.verify_webhook_signature(body, expected, timestamp)
        assert result is True

    def test_verify_invalid_signature(self, app_manager, test_user):
        """Test that invalid signature raises error."""
        app_manager.create_application(owner_id=test_user.id, name="Bad Sig App")
        body = b'{"type": 1}'
        timestamp = str(int(time.time()))
        bad_signature = "invalid_signature"

        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(body, bad_signature, timestamp)

    def test_verify_tampered_body(self, app_manager, test_user):
        """Test that tampered body causes signature mismatch."""
        app_manager.create_application(owner_id=test_user.id, name="Tamper App")
        original_body = b'{"type": 1}'
        timestamp = str(int(time.time()))
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = timestamp.encode() + original_body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        tampered_body = b'{"type": 2}'
        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(tampered_body, expected, timestamp)

    def test_verify_wrong_timestamp(self, app_manager, test_user):
        """Test that wrong timestamp causes signature mismatch."""
        app_manager.create_application(owner_id=test_user.id, name="Time App")
        body = b'{"type": 1}'
        original_timestamp = str(int(time.time()))
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = original_timestamp.encode() + body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        wrong_timestamp = "9999999999"
        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(body, expected, wrong_timestamp)

    def test_timestamp_too_old_rejected(self, app_manager, test_user):
        """Test that old timestamps are rejected to prevent replay attacks."""
        app_manager.create_application(owner_id=test_user.id, name="Replay App")
        body = b'{"type": 1}'
        old_timestamp = str(int(time.time()) - 600)
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = old_timestamp.encode() + body
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        with pytest.raises(WebhookSignatureError, match="Timestamp outside"):
            app_manager.verify_webhook_signature(body, signature, old_timestamp)

    def test_timestamp_too_far_future_rejected(self, app_manager, test_user):
        """Test that future timestamps are rejected."""
        app_manager.create_application(owner_id=test_user.id, name="Future App")
        body = b'{"type": 1}'
        future_timestamp = str(int(time.time()) + 600)
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = future_timestamp.encode() + body
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        with pytest.raises(WebhookSignatureError, match="Timestamp outside"):
            app_manager.verify_webhook_signature(body, signature, future_timestamp)

    def test_invalid_timestamp_format_rejected(self, app_manager, test_user):
        """Test that non-numeric timestamps are rejected."""
        app_manager.create_application(owner_id=test_user.id, name="BadTime App")
        body = b'{"type": 1}'

        with pytest.raises(WebhookSignatureError, match="Invalid webhook timestamp"):
            app_manager.verify_webhook_signature(body, "sig", "not_a_number")


class TestWebhookSecretPersistence:
    def test_unpersistable_secret_hard_fails_startup(self, monkeypatch):
        """Persistence failure retries then HARD-FAILS instead of warn-continue.

        Warn-and-continue would leave each process with a different in-memory
        signing secret (divergent webhook verification per worker).
        """
        import utils.config as config

        calls = {"n": 0}

        def boom(key, value):
            calls["n"] += 1
            raise OSError("disk on fire")

        monkeypatch.setattr(config, "get", lambda key, default=None: {})
        monkeypatch.setattr(config, "set", boom)
        monkeypatch.setattr(
            "src.core.applications.manager.base.time.sleep", lambda s: None
        )

        from src.core.applications.manager.base import ApplicationManager

        with pytest.raises(RuntimeError, match="webhook signing secret"):
            ApplicationManager._resolve_webhook_secret({})
        assert calls["n"] == 3
