"""Focused regression tests for the wave of boundary hardening fixes in the
DSAR / push / routes / webhooks area.

Each guard rejects input that previously reached an outbound transport, a SQL
statement or a storage lookup unchecked. These tests pin the rejection so a
later refactor cannot quietly drop it.
"""

import sys
import types

import pytest

pytestmark = pytest.mark.unit


def _bare_push_manager():
    """A PushManager instance with no DB work, for pure sender tests."""
    from src.core.push.manager import PushManager

    mgr = object.__new__(PushManager)
    mgr._vapid_available = True
    mgr._fcm_available = False
    mgr._apns_available = False
    return mgr


def _fake_pywebpush(monkeypatch):
    calls = []

    fake = types.ModuleType("pywebpush")

    class WebPushException(Exception):
        pass

    def webpush(**kwargs):
        calls.append(kwargs)

    fake.WebPushException = WebPushException
    fake.webpush = webpush
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    return calls


class TestWebPushEndpoint:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "/relative/path",
            "push.example/ep",
            "http://push.example/ep",
            "https://user:pass@push.example/ep",
            "javascript:alert(1)",
        ],
    )
    def test_non_https_or_credentialed_endpoint_is_refused(self, monkeypatch, endpoint):
        calls = _fake_pywebpush(monkeypatch)
        mgr = _bare_push_manager()
        rec = {"token": endpoint, "p256dh": "key", "auth": "secret"}
        assert mgr._send_web_push(rec, "t", "b", None, {}) is None
        assert calls == []

    def test_https_endpoint_is_delivered(self, monkeypatch):
        calls = _fake_pywebpush(monkeypatch)
        mgr = _bare_push_manager()
        rec = {
            "token": "https://push.example/ep",
            "p256dh": "key",
            "auth": "secret",
        }
        config = {"vapid_private_key": "k", "vapid_subject": "mailto:a@b.c"}
        assert mgr._send_web_push(rec, "t", "b", None, config) is True
        assert calls[0]["subscription_info"]["endpoint"] == "https://push.example/ep"


class TestApnsToken:
    @pytest.mark.parametrize(
        "token",
        [
            "abc/../../admin",
            "abc?topic=evil",
            "abc#frag",
            "user:pass@host",
            "abc def",
            "a" * 300,
            "",
        ],
    )
    def test_structural_token_is_refused(self, monkeypatch, token):
        from src.core.push.manager.apns import _safe_apns_token

        with pytest.raises(ValueError):
            _safe_apns_token(token)

    @pytest.mark.parametrize("token", ["device-token", "tok", "A" * 64, "a.b_c-1"])
    def test_normal_token_is_accepted(self, token):
        from src.core.push.manager.apns import _safe_apns_token

        assert _safe_apns_token(token) == token


class TestPushCredentialBounds:
    def test_oversized_token_is_refused(self):
        from src.core.push.manager.tokens import MAX_TOKEN_LENGTH, _bounded_credential

        with pytest.raises(ValueError):
            _bounded_credential("x" * (MAX_TOKEN_LENGTH + 1), "token", MAX_TOKEN_LENGTH)

    def test_required_empty_is_refused(self):
        from src.core.push.manager.tokens import _bounded_credential

        with pytest.raises(ValueError):
            _bounded_credential("", "token", 100, required=True)
        with pytest.raises(ValueError):
            _bounded_credential(None, "token", 100, required=True)

    def test_optional_none_passes(self):
        from src.core.push.manager.tokens import _bounded_credential

        assert _bounded_credential(None, "p256dh", 100) is None

    def test_in_range_passes(self):
        from src.core.push.manager.tokens import _bounded_credential

        assert _bounded_credential("abc", "token", 100) == "abc"


class TestPushTextBounds:
    def test_overlong_title_is_refused_before_fanout(self):
        from src.core.push.manager.delivery import MAX_TITLE_LENGTH, _bounded_text

        with pytest.raises(ValueError):
            _bounded_text("x" * (MAX_TITLE_LENGTH + 1), "title", MAX_TITLE_LENGTH)

    def test_overlong_body_is_refused(self):
        from src.core.push.manager.delivery import MAX_BODY_LENGTH, _bounded_text

        with pytest.raises(ValueError):
            _bounded_text("x" * (MAX_BODY_LENGTH + 1), "body", MAX_BODY_LENGTH)

    def test_normal_text_passes(self):
        from src.core.push.manager.delivery import _bounded_text

        assert _bounded_text("hello", "title", 256) == "hello"


class TestWebhookAvatarCredentials:
    def test_credentialed_avatar_url_is_refused(self):
        from src.core.webhooks.manager.validation import ValidationMixin
        from src.core.webhooks.exceptions import WebhookAvatarError

        with pytest.raises(WebhookAvatarError):
            object.__new__(ValidationMixin).__class__._validate_avatar_url(
                object.__new__(ValidationMixin), "https://user:pass@example.com/a.png"
            )

    def test_plain_avatar_url_has_no_credentials(self):
        from urllib.parse import urlparse

        parsed = urlparse("https://example.com/a.png")
        assert parsed.username is None and parsed.password is None
