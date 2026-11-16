"""Deep hardening: fail closed on missing or malformed auth context.

A missing/non-string/oversized credential used to blow up in the cached
verify_token key builder (a 500 instead of a denial), and a malformed explicit
cookie expiry silently fell back to the long default lifetime. These tests
prove the denial paths: bad credentials raise TokenInvalidError without a DB
lookup, token binding denies absent context, and bad expiries get a minimal
cookie lifetime.
"""

import pytest

from src.api.routes.auth import cookies as cookie_mod
from src.core.auth import tokens
from src.core.auth.exceptions import TokenInvalidError


@pytest.mark.parametrize(
    "bad",
    [
        None,
        b"123456789.abcdefghijklmnopqrstuvwxyzabcdefg",
        12345,
        "",
        "x" * (tokens.MAX_TOKEN_LENGTH + 1),
    ],
)
def test_verify_token_denies_malformed_context(auth_manager, bad):
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(bad)


def test_cache_key_builder_is_safe_for_bad_input():
    assert tokens.safe_token_cache_key(None) == "token_verify:invalid"
    assert tokens.safe_token_cache_key(b"bytes") == "token_verify:invalid"
    good = "123456789." + tokens.generate_token_secret(32)
    key = tokens.safe_token_cache_key(good)
    assert key.startswith("token_verify:str:")
    assert good not in key  # raw credential never appears in the cache key


def test_malformed_cookie_expiry_fails_closed():
    for bad in ["not-a-number", object(), float("nan")]:
        assert cookie_mod.session_cookie_max_age(bad) == 1
    # A sound expiry is still honoured.
    import time

    now = int(time.time() * 1000)
    assert cookie_mod.session_cookie_max_age(now + 3600_000) == pytest.approx(
        3600, abs=2
    )


def test_missing_binding_context_is_denied(auth_manager, test_user_with_token, monkeypatch):
    manager = auth_manager._manager
    monkeypatch.setattr(
        manager,
        "_config",
        {
            **manager._config,
            "security": {**manager._config.get("security", {}), "token_binding": True},
        },
    )
    from unittest.mock import patch

    with patch("src.utils.encryption.verify_password", return_value=True):
        result = auth_manager.login(
            test_user_with_token["username"], test_user_with_token["password"]
        )

    # No IP / UA at all -> denied.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(result.token)
    # IP but no UA -> still denied (binding requires a complete context).
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(result.token, ip_address="203.0.113.10")
    # With a full context the same token verifies.
    assert auth_manager.verify_token(
        result.token, ip_address="203.0.113.10", user_agent="test-agent/1.0"
    ).valid is True


def test_empty_bearer_and_bad_cookie_denied_at_route(auth_manager):
    """The refresh route must not treat an empty/foreign credential as valid."""
    for bad in ["", "   ", "basic abc", "1.short"]:
        assert auth_manager.refresh_session(bad) is None
