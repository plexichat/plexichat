"""Regression tests for profile field lengths and URL validation."""

import pytest

from src.core.profiles.manager import (
    MAX_PROFILE_URL_LENGTH,
    MAX_SOCIAL_LINK_URL_LENGTH,
    ProfileManager,
    _bounded_text,
    _validate_profile_url,
)
from src.core.security.hardening import HardeningViolation


class _DB:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetch_one(self, sql, params=()):
        return None

    def fetch_all(self, sql, params=()):
        return []


def _manager():
    return ProfileManager(_DB())


class TestFieldLengthBounds:
    def test_bounded_text(self):
        assert _bounded_text("  hi  ", "bio", 100) == "hi"
        with pytest.raises(HardeningViolation):
            _bounded_text("x" * 101, "bio", 100)
        with pytest.raises(HardeningViolation):
            _bounded_text("a\x00b", "bio", 100)

    def test_bio_overflow_rejected(self):
        manager = _manager()
        with pytest.raises(HardeningViolation):
            manager.update_profile(1, bio="x" * (manager.MAX_BIO_LENGTH + 1))

    def test_social_link_url_overflow_rejected(self):
        manager = _manager()
        with pytest.raises(HardeningViolation):
            manager._validate_social_links(
                [
                    {
                        "platform": "github",
                        "url": "https://x.co/" + "a" * MAX_SOCIAL_LINK_URL_LENGTH,
                    }
                ]
            )

    def test_social_link_platform_overflow_rejected(self):
        manager = _manager()
        with pytest.raises(HardeningViolation):
            manager._validate_social_links(
                [{"platform": "x" * 33, "url": "https://example.com"}]
            )


class TestProfileUrlValidation:
    def test_valid_https_url(self):
        assert (
            _validate_profile_url("https://cdn.example.com/a.png", "banner_url")
            == "https://cdn.example.com/a.png"
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "javascript:alert(1)",
            "data:text/html,x",
            "file:///etc/passwd",
            "ftp://example.com/x",
            "http://user:pass@example.com/",
            "http://127.0.0.1/",
            "http://169.254.169.254/",
            "http://localhost/",
            "not a url at all",
        ],
    )
    def test_unsafe_url_rejected(self, bad):
        with pytest.raises(ValueError):
            _validate_profile_url(bad, "banner_url")

    def test_banner_url_overflow_rejected(self):
        manager = _manager()
        with pytest.raises(HardeningViolation):
            manager.update_profile(
                1, banner_url="https://example.com/" + "a" * MAX_PROFILE_URL_LENGTH
            )

    def test_banner_internal_host_rejected(self):
        manager = _manager()
        with pytest.raises(ValueError):
            manager.update_profile(1, banner_url="http://127.0.0.1/secret")
