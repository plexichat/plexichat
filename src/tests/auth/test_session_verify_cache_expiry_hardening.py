"""Session verification cache must not outlive the credential's expiry.

``VerificationMixin.verify_token`` memoises the DB-backed verdict for a short
TTL. The expiry check lives inside the cached callee, so without a re-check on
the (uncached) wrapper a token that expires *inside* the TTL window keeps
authenticating until the entry is evicted. This test pins that an expiry
reached after a cache warm is enforced on the cache-hit path as well.
"""

from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenExpiredError
from src.core.auth.managers.session.verification import VerificationMixin
from src.core.auth.models import AccountType, TokenInfo
from src.core.database import invalidate_pattern

_TOKEN = "123456789." + "a" * 43
_PARSED = {"token_type": "session", "id": 123456789, "secret": "a" * 43}


class _Clock:
    """Stand-in for the ``time`` module used by ``is_expired`` in tokens.py."""

    def __init__(self):
        self.now_ms = 1_700_000_000_000

    def time(self):
        return self.now_ms / 1000.0


def _info(expires_at):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=7,
        user_id=7,
        session_id="s-1",
        username="u",
        permissions={},
        account_type=AccountType.USER,
        rate_limit_tier="standard",
        expires_at=expires_at,
    )


class _Stub(VerificationMixin):
    def __init__(self):
        self._config = {"security": {}, "sessions": {}}
        self._db = object()


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    invalidate_pattern("token_verify:*")
    yield
    invalidate_pattern("token_verify:*")


def test_cached_verdict_cannot_outlive_session_expiry():
    """A cache hit past ``expires_at`` must be denied, not replayed."""
    stub = _Stub()
    clock = _Clock()
    expires_ms = clock.now_ms + 5_000  # expiry lands inside the 10s verify TTL
    state = {"verify_calls": 0}

    def fake_verify_session_token(*a, **k):
        # Mimic the real verifier: it denies once the stored expiry has passed.
        state["verify_calls"] += 1
        if clock.now_ms >= expires_ms:
            raise TokenExpiredError("Expired")
        return _info(expires_ms)

    with (
        patch(
            "src.core.auth.managers.session.verification.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            VerificationMixin,
            "_verify_session_token",
            side_effect=fake_verify_session_token,
        ),
        patch("src.core.auth.tokens.time", clock),
    ):
        # Warm the cache while the credential is still valid.
        warmed = stub.verify_token(_TOKEN)
        assert warmed.valid is True
        assert state["verify_calls"] == 1

        # Advance past expiry but stay inside the cache TTL. A second call must
        # hit the cache (verifier not called again) yet still be denied.
        clock.now_ms = expires_ms + 1_000
        with pytest.raises(TokenExpiredError):
            stub.verify_token(_TOKEN)
        assert state["verify_calls"] == 1  # proves the denial came from a cache hit


def test_cached_verdict_expiry_fails_closed_on_unparseable_value():
    """A corrupt cached expiry is treated as expired, never as a grant."""
    stub = _Stub()
    clock = _Clock()
    state = {"verify_calls": 0}

    def fake_verify_session_token(*a, **k):
        state["verify_calls"] += 1
        return _info("not-a-timestamp")

    with (
        patch(
            "src.core.auth.managers.session.verification.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            VerificationMixin,
            "_verify_session_token",
            side_effect=fake_verify_session_token,
        ),
        patch("src.core.auth.tokens.time", clock),
    ):
        with pytest.raises(TokenExpiredError):
            stub.verify_token(_TOKEN)
        assert state["verify_calls"] == 1
