"""Redirect URIs are literal callback addresses, never patterns.

A wildcard, a trailing-slash fold or a look-alike subdomain would turn the
OAuth allow-list into an open redirector and an authorization-code interception
primitive. These tests pin exact-string matching at both layers.
"""

import pytest

from src.core.applications.validation import (
    require_exact_redirect_match,
    validate_redirect_uris,
)
from src.core.security.hardening import HardeningViolation

REGISTERED = "https://app.example.com/oauth/callback"


class TestCoreExactMatch:
    def test_exact_registration_is_accepted(self):
        assert require_exact_redirect_match(REGISTERED, [REGISTERED]) == REGISTERED

    @pytest.mark.parametrize(
        "candidate",
        [
            REGISTERED + "/",  # trailing-slash fold
            REGISTERED.replace("app.", "evil-app."),  # look-alike subdomain
            REGISTERED.replace("app.example.com", "example.com.evil.test"),
            REGISTERED.replace("https", "http"),  # scheme downgrade
            REGISTERED.upper(),  # case folding
            REGISTERED + "/extra",
            REGISTERED + "?next=https://evil.test",
            "https://app.example.com/*",  # wildcard
            "https://*.example.com/oauth/callback",
            REGISTERED + "#fragment",
            "",
            None,
        ],
    )
    def test_non_identical_or_pattern_candidates_rejected(self, candidate):
        with pytest.raises(HardeningViolation):
            require_exact_redirect_match(candidate, [REGISTERED])

    def test_pattern_like_registration_never_matches_anything(self):
        # Even if a pattern somehow reached storage, it must not be interpreted
        # as a wildcard: only literal equality is allowed.
        with pytest.raises(HardeningViolation):
            require_exact_redirect_match(
                "https://app.example.com/anything",
                ["https://app.example.com/*"],
            )

    def test_allow_list_cannot_be_a_string(self):
        with pytest.raises(HardeningViolation):
            require_exact_redirect_match(REGISTERED, REGISTERED)


class TestRegistrationRejectsPatterns:
    @pytest.mark.parametrize(
        "uri",
        [
            "https://app.example.com/*",
            "https://*.example.com/callback",
            "https://app.example.com/cb[0-9]",
            "https://app.example.com/{id}",
            "https://app.example.com/cb|https://evil.test",
        ],
    )
    def test_wildcard_entries_rejected(self, uri):
        with pytest.raises(HardeningViolation):
            validate_redirect_uris([uri])


class TestProviderRouteExactMatch:
    def _allowed(self, monkeypatch, allowed):
        import src.api.routes.auth.oauth as route

        monkeypatch.setattr(
            route.config_util,
            "get",
            lambda key, default=None: (
                {"google": {"allowed_redirect_uris": allowed}}
                if key == "oauth"
                else default
            ),
        )
        return route._redirect_uri_is_allowed

    def test_only_literal_entry_allowed(self, monkeypatch):
        allowed = self._allowed(monkeypatch, [REGISTERED])
        assert allowed("google", REGISTERED) is True
        assert allowed("google", REGISTERED + "/") is False
        assert allowed("google", REGISTERED.replace("app.", "evil.")) is False
        assert allowed("google", "https://app.example.com/other") is False

    def test_pattern_entries_never_match(self, monkeypatch):
        allowed = self._allowed(monkeypatch, ["https://app.example.com/*"])
        assert allowed("google", "https://app.example.com/anything") is False
