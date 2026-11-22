"""Second-opinion: redirect_uri exact-match survives prefix/suffix tricks.

The OAuth allow-list is a literal string comparison at every layer. A look-alike
host, userinfo smuggling, a trailing dot, a port variation, path traversal or a
percent-encoded character is a *different string* from the registered callback
and must never be accepted as it. The state (and, for app OAuth, the code) must
also be bound to the exact redirect_uri presented at the start of the flow, so
a valid state cannot be replayed with a different callback.

These are the adversarial variants called out in the redirect-URI audit; pin
them here so a future change to normalise/fold the comparison is caught.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.api.routes.auth.oauth import _redirect_uri_is_allowed
from src.core.applications.validation import require_exact_redirect_match
from src.core.auth.oauth.state import OAuthStateManager
from src.core.security.hardening import HardeningViolation

REGISTERED = "https://allowed.com/oauth/callback"
PROVIDER = "google"
CONFIG_SNAPSHOT = {PROVIDER: {"allowed_redirect_uris": [REGISTERED]}}

# Each entry is a string that is NOT byte-for-byte the registered callback.
TRICK_URIS = [
    "https://allowed.com.evil.com/oauth/callback",  # suffix subdomain
    "https://allowed.com@evil.com/oauth/callback",  # userinfo -> evil host
    "https://evil.com@allowed.com/oauth/callback",  # userinfo -> allowed host
    "https://allowed.com/../evil",  # path traversal
    "https://allowed.com/oauth/%63allback",  # percent-encoded path
    "https://allowed.com%2eevil.com/oauth/callback",  # percent-encoded dot
    "https://allowed.com./oauth/callback",  # trailing dot in host
    REGISTERED + "#fragment",
    REGISTERED + "?next=https://evil.com",
    "https://allowed.com:8443/oauth/callback",  # port variation
    "http://allowed.com/oauth/callback",  # scheme downgrade
    "https://ALLOWED.com/oauth/callback",  # case folding
    REGISTERED + "/",  # trailing slash
    "https://allowed.com/*",  # wildcard
    "https://allowed.com\\@evil.com/oauth/callback",  # backslash authority
]


class _MemoryDB:
    """Minimal Database stand-in for the state manager."""

    type = "sqlite"

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE auth_oauth_states (
                id INTEGER PRIMARY KEY, state_hash TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL, redirect_uri TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                used INTEGER DEFAULT 0, nonce_hash TEXT, pkce_challenge TEXT,
                ip_address TEXT, cookie_issued INTEGER DEFAULT 0)"""
        )
        self._conn.commit()

    def convert_schema(self, schema: str) -> str:
        return schema

    def execute(self, sql, params=()):
        return self._conn.execute(sql, params)

    def fetch_one(self, sql, params=()):
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None


def _manager() -> OAuthStateManager:
    return OAuthStateManager(
        _MemoryDB(), {"max_states_per_ip": 0, "cleanup_on_verify": False}
    )


class TestRouteAllowList:
    def test_registered_uri_accepted(self):
        assert _redirect_uri_is_allowed(PROVIDER, REGISTERED, CONFIG_SNAPSHOT) is True

    @pytest.mark.parametrize("candidate", TRICK_URIS)
    def test_trick_uris_rejected(self, candidate):
        assert _redirect_uri_is_allowed(PROVIDER, candidate, CONFIG_SNAPSHOT) is False


class TestCoreExactMatch:
    def test_registered_uri_accepted(self):
        assert require_exact_redirect_match(REGISTERED, [REGISTERED]) == REGISTERED

    @pytest.mark.parametrize("candidate", TRICK_URIS)
    def test_trick_uris_rejected(self, candidate):
        with pytest.raises(HardeningViolation):
            require_exact_redirect_match(candidate, [REGISTERED])


class TestStateRedirectBinding:
    def test_exact_uri_consumes_state(self):
        manager = _manager()
        state = manager.create_state(PROVIDER, REGISTERED)
        assert state is not None and state.state_token
        valid, _, error = manager.verify_state(state.state_token, PROVIDER, REGISTERED)
        assert valid is True
        assert error is None

    @pytest.mark.parametrize("candidate", TRICK_URIS)
    def test_state_cannot_be_replayed_with_different_uri(self, candidate):
        manager = _manager()
        state = manager.create_state(PROVIDER, REGISTERED)
        assert state is not None and state.state_token
        valid, _, error = manager.verify_state(state.state_token, PROVIDER, candidate)
        assert valid is False
        assert error == "State token redirect URI mismatch"
