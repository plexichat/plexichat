"""Webhook/interaction URL and configuration constraints."""

import hashlib
import hmac
import time

import pytest

from src.core.applications.validation import (
    MAX_URL_PATTERN_LENGTH,
    MAX_WEBHOOK_URL_LENGTH,
    validate_config_url_pattern,
    validate_webhook_url,
)
from src.core.applications.exceptions import WebhookSignatureError
from src.core.security.hardening import HardeningViolation


class TestWebhookUrls:
    def test_accepts_https(self):
        url = "https://example.com/interactions"
        assert validate_webhook_url(url) == url
        assert validate_webhook_url(None) is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/interactions",
            "https://user:pw@example.com/hook",
            "https://example.com/hook#frag",
            "/relative",
            "javascript:alert(1)",
            "https://example.com/" + "a" * MAX_WEBHOOK_URL_LENGTH,
            "https://example.com/\nhook",
        ],
    )
    def test_rejects_unsafe_urls(self, url):
        with pytest.raises(HardeningViolation):
            validate_webhook_url(url)


class TestConfigPatterns:
    def test_valid_pattern(self):
        assert validate_config_url_pattern("^https?://") == "^https?://"
        assert validate_config_url_pattern(None) is None

    @pytest.mark.parametrize(
        "pattern", ["[", "(" + "a" * MAX_URL_PATTERN_LENGTH, "bad\npattern"]
    )
    def test_rejects_bad_patterns(self, pattern):
        with pytest.raises(HardeningViolation):
            validate_config_url_pattern(pattern)

    def test_manager_config_is_validated(self, app_manager, monkeypatch):
        import src.core.applications.manager.base as base_module

        monkeypatch.setattr(
            base_module.config,
            "get",
            lambda key, default=None: (
                {
                    "oauth": {"allowed_redirect_uri_pattern": "["},
                    "webhook_signature_secret": "test-secret",
                }
                if key == "applications"
                else default
            ),
        )
        with pytest.raises(HardeningViolation):
            app_manager._load_config()


class TestManagerAndSignature:
    def test_create_and_update_reject_bad_endpoint(self, app_manager, test_user):
        with pytest.raises(HardeningViolation):
            app_manager.create_application(
                owner_id=test_user.id,
                name="Bad Endpoint",
                interactions_endpoint_url="http://example.com/hook",
            )
        app = app_manager.create_application(
            owner_id=test_user.id, name="Good Endpoint"
        )
        with pytest.raises(HardeningViolation):
            app_manager.update_application(
                user_id=test_user.id,
                application_id=app.id,
                interactions_endpoint_url="file:///etc/passwd",
            )

    def test_oversized_signature_rejected(self, app_manager):
        app_manager._config["webhook_signature_secret"] = "test-secret"
        ts = str(int(time.time()))
        body = b"payload"
        expected = hmac.new(
            b"test-secret", ts.encode() + body, hashlib.sha256
        ).hexdigest()
        assert app_manager.verify_webhook_signature(body, expected, ts) is True
        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(body, "a" * 129, ts)

    def test_source_uses_bounded_skew(self):
        import inspect

        from src.core.applications.manager.webhook import WebhookMixin

        source = inspect.getsource(WebhookMixin.verify_webhook_signature)
        assert "bounded_int" in source
        assert "MAX_SIGNATURE_LENGTH" in source
