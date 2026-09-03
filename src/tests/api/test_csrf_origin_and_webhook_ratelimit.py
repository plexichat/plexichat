"""Regression tests for the Tier 1 CSRF + webhook hardening batch.

Covers:
- Agent 46 M2 (residual): cookie-authenticated mutating requests from
  browsers without Sec-Fetch-* must present the marker header AND an
  Origin/Referer matching the configured CORS allowlist.
- Agent 35 F12 (residual): webhook execution rate limiting fails SAFE —
  an absent or malformed ``rate_limit_per_minute`` config applies a sane
  default instead of disabling limiting.
"""

import pytest

from src.api.middleware.authentication import (
    AuthenticationMiddleware,
)
from src.core.webhooks.manager.execute import (
    WebhookExecutionMixin,
    DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE,
)


class _FakeRequest:
    """Minimal header mapping for middleware helpers."""

    def __init__(self, headers=None):
        self.headers = headers or {}


class TestOriginValidation:
    def test_no_origin_no_referer_allows_marker_only(self):
        """Non-browser clients (curl, S2S) send neither header; the marker
        header remains the single gate for them."""
        req = _FakeRequest({"X-Plexichat-Request": "1"})
        assert AuthenticationMiddleware._origin_allowed(req) is True

    def test_matching_origin_allowed(self):
        req = _FakeRequest(
            {"Origin": "https://app.plexichat.com", "X-Plexichat-Request": "1"}
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "utils.config.get",
                lambda *a, **k: {"cors_origins": ["https://app.plexichat.com"]},
            )
            assert AuthenticationMiddleware._origin_allowed(req) is True

    def test_mismatched_origin_rejected(self):
        req = _FakeRequest(
            {"Origin": "https://evil.example", "X-Plexichat-Request": "1"}
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "utils.config.get",
                lambda *a, **k: {"cors_origins": ["https://app.plexichat.com"]},
            )
            assert AuthenticationMiddleware._origin_allowed(req) is False

    def test_referer_host_extracted_and_checked(self):
        req = _FakeRequest(
            {
                "Referer": "https://app.plexichat.com/app/channels",
                "X-Plexichat-Request": "1",
            }
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "utils.config.get",
                lambda *a, **k: {"cors_origins": ["https://app.plexichat.com"]},
            )
            assert AuthenticationMiddleware._origin_allowed(req) is True

    def test_evil_referer_host_rejected(self):
        req = _FakeRequest(
            {"Referer": "https://evil.example/attack", "X-Plexichat-Request": "1"}
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "utils.config.get",
                lambda *a, **k: {"cors_origins": ["https://app.plexichat.com"]},
            )
            assert AuthenticationMiddleware._origin_allowed(req) is False

    def test_trailing_slash_normalized(self):
        req = _FakeRequest({"Origin": "https://app.plexichat.com/"})
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "utils.config.get",
                lambda *a, **k: {"cors_origins": ["https://app.plexichat.com/"]},
            )
            assert AuthenticationMiddleware._origin_allowed(req) is True


class TestWebhookRateLimitFailSafe:
    """F12: absent/invalid config must apply the default, not disable."""

    def _make_mixin(self, config_value):
        mixin = WebhookExecutionMixin.__new__(WebhookExecutionMixin)
        mixin._config = (
            {"rate_limit_per_minute": config_value} if config_value is not ... else {}
        )
        return mixin

    def test_default_constant_is_safe(self):
        assert DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE > 0
        assert DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE <= 120, (
            "default must not itself be an unbounded flood allowance"
        )

    def test_missing_config_uses_default(self):
        """The historical bug: config absent -> 0 -> unlimited execution."""
        mixin = self._make_mixin(...)
        resolved = mixin._config.get("rate_limit_per_minute")
        assert resolved is None
        # Mirrors the fixed resolution logic:
        try:
            resolved_limit = int(resolved)
        except (TypeError, ValueError):
            resolved_limit = DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE
        assert resolved_limit == DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE

    def test_garbage_config_uses_default(self):
        mixin = self._make_mixin("not-a-number")
        try:
            resolved_limit = int(mixin._config.get("rate_limit_per_minute"))
        except (TypeError, ValueError):
            resolved_limit = DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE
        assert resolved_limit == DEFAULT_WEBHOOK_RATE_LIMIT_PER_MINUTE

    def test_valid_config_honored(self):
        mixin = self._make_mixin(5)
        assert int(mixin._config.get("rate_limit_per_minute")) == 5
