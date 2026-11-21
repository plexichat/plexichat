"""Regression tests for the bounded, binding-aware token-verification cache.

One credential is routinely verified several times for a single request or
gateway connection (auth middleware, then a dependency, then a media/WS
helper). ``verify_token`` therefore memoises the DB-backed result for a short
TTL under a key derived from a *hash* of the token and the presented IP/UA.

These tests pin that the cache is a pure optimisation: rate limiting,
revocation, IP/User-Agent binding and fail-closed denial are all preserved, and
no credential (or other user's context) can leak across cache entries.
"""

from unittest.mock import patch

import pytest

from src.core.auth import tokens
from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.managers.session.verification import VerificationMixin
from src.core.auth.models import AccountType, TokenInfo
from src.core.database import invalidate_pattern

# The credential below is structurally valid but never echoed by a test or a
# failure message; only its digest ever reaches a cache key.
_TOKEN = "123456789." + "a" * 43
_PARSED = {"token_type": "session", "id": 123456789, "secret": "a" * 43}
_IP = "203.0.113.10"
_UA = "regression-agent/1.0"


def _info(user_id=7):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id="s-1",
        username="u",
        permissions={},
        account_type=AccountType.USER,
        rate_limit_tier="standard",
        expires_at=4102444800000,
    )


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    invalidate_pattern("token_verify:*")
    yield
    invalidate_pattern("token_verify:*")


class _Stub(VerificationMixin):
    """Minimal host for the verification mixin used by the cache-hit tests."""

    def __init__(self, config):
        self._config = config
        self._db = object()


def _production_like_config(binding=False):
    return {
        "security": {
            "token_verify_rate_limit": 100,
            "token_binding": binding,
        },
        "sessions": {},
    }


# ---------------------------------------------------------------------------
# Correctness / optimisation: the same credential is verified once.
# ---------------------------------------------------------------------------


def test_repeated_verification_hits_cache_but_rate_limit_still_runs():
    stub = _Stub(_production_like_config())
    info = _info()
    calls = {"verify": 0, "rate": 0}

    def count_verify(*a, **k):
        calls["verify"] += 1
        return info

    def count_rate(key, limit, window_seconds):
        calls["rate"] += 1
        return True, limit

    with (
        patch(
            "src.core.auth.managers.session.verification.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            VerificationMixin, "_verify_session_token", side_effect=count_verify
        ),
        patch("src.core.database.cache.check_rate_limit", side_effect=count_rate),
    ):
        results = [
            stub.verify_token(_TOKEN, ip_address=_IP, user_agent=_UA) for _ in range(5)
        ]

    assert all(r.valid for r in results)
    assert all(r.user_id == info.user_id for r in results)
    # One DB-backed verification, but the throttle ran on every call.
    assert calls["verify"] == 1
    assert calls["rate"] == 5


def test_rate_limit_denial_beats_a_warm_cache_hit():
    """A cached verification cannot be used to skip the rate limiter."""
    stub = _Stub(_production_like_config())
    info = _info()
    calls = {"verify": 0}

    def count_verify(*a, **k):
        calls["verify"] += 1
        return info

    with (
        patch(
            "src.core.auth.managers.session.verification.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            VerificationMixin, "_verify_session_token", side_effect=count_verify
        ),
        patch("src.core.database.cache.check_rate_limit", return_value=(True, 99)),
    ):
        assert stub.verify_token(_TOKEN, _IP, _UA).valid is True
        assert calls["verify"] == 1

    # Now the limiter denies. Even with a warm cache entry, verification must
    # fail closed without consuming it.
    with (
        patch(
            "src.core.auth.managers.session.verification.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            VerificationMixin, "_verify_session_token", side_effect=count_verify
        ),
        patch("src.core.database.cache.check_rate_limit", return_value=(False, 0)),
    ):
        with pytest.raises(TokenInvalidError):
            stub.verify_token(_TOKEN, _IP, _UA)
        assert calls["verify"] == 1  # no DB verification happened on denial


def test_malformed_context_fails_closed_without_touching_the_cache():
    stub = _Stub(_production_like_config())
    with patch.object(VerificationMixin, "_verify_session_token") as verify:
        for bad in (None, b"bytes", 12345, "", "x" * (tokens.MAX_TOKEN_LENGTH + 1)):
            with pytest.raises(TokenInvalidError):
                stub.verify_token(bad)
        verify.assert_not_called()


# ---------------------------------------------------------------------------
# Cache key hygiene: hashed token, binding-scoped, bounded length.
# ---------------------------------------------------------------------------


def test_cache_key_is_hashed_and_binding_scoped():
    base = tokens.safe_token_cache_key(_TOKEN)
    assert _TOKEN not in base
    assert base.startswith("token_verify:str:")

    key_ip_ua = base + tokens.safe_token_binding_key(_IP, _UA)
    key_other = base + tokens.safe_token_binding_key("203.0.113.11", "attacker/1.0")
    assert ":b:" in key_ip_ua
    assert key_ip_ua != key_other
    # No raw IP/UA in the keyspace and the suffix has a fixed, short length.
    assert _IP not in key_ip_ua and _UA not in key_ip_ua
    assert len(tokens.safe_token_binding_key(_IP, _UA)) == 3 + 32
    assert tokens.safe_token_binding_key(None, None) == ""


# ---------------------------------------------------------------------------
# Security invariants against a real manager / database.
# ---------------------------------------------------------------------------


def test_revocation_invalidates_a_warm_cached_verification(
    auth_manager, test_user_with_token
):
    token = test_user_with_token["token"]
    user_id = test_user_with_token["user"].id

    warmed = auth_manager.verify_token(token)
    assert warmed.valid is True

    session_id = int(warmed.session_id)
    assert auth_manager.revoke_session(user_id, session_id) is True

    # The cached (valid=True) TokenInfo must not survive the revoke.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token)


def test_binding_context_cannot_be_replayed_from_another_ip_or_ua(
    auth_manager, test_user_with_token, monkeypatch
):
    manager = auth_manager._manager
    monkeypatch.setattr(
        manager,
        "_config",
        {
            **manager._config,
            "security": {
                **manager._config.get("security", {}),
                "token_binding": True,
            },
        },
    )
    user_id = test_user_with_token["user"].id
    good_ip, good_ua = "203.0.113.10", "legit-agent/1.0"
    bad_ip, bad_ua = "198.51.100.77", "attacker/9.9"

    result = auth_manager.create_session_for_user(
        user_id, ip_address=good_ip, user_agent=good_ua
    )
    token = result.token

    # Warm the cache from the legitimate binding context.
    assert auth_manager.verify_token(token, good_ip, good_ua).valid is True

    # The same bearer from a different IP/UA must not be served from that cache
    # entry; it reaches the verifier and fails the binding check.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token, bad_ip, bad_ua)
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token, good_ip, bad_ua)

    # The original context still verifies (its own cache entry is intact).
    assert auth_manager.verify_token(token, good_ip, good_ua).valid is True


def test_no_cross_user_leak_between_cached_tokens(auth_manager, test_user_with_token):
    other = auth_manager.register(
        username="cache_other_user",
        email="cache_other_user@example.com",
        password="TestPass123!",
    )
    other_result = auth_manager.login("cache_other_user", "TestPass123!")
    first_token = test_user_with_token["token"]
    first_user = test_user_with_token["user"].id

    first = auth_manager.verify_token(first_token)
    second = auth_manager.verify_token(other_result.token)

    assert first.valid and second.valid
    assert first.user_id == first_user
    assert second.user_id == other.id
    assert first.user_id != second.user_id
    # Re-reading the first token after the second was cached still yields the
    # first user's context, not the other user's.
    assert auth_manager.verify_token(first_token).user_id == first_user
