"""N712 follow-up: the shadowed LifecycleMixin verifier must re-check expiry.

``VerificationMixin.verify_token`` (the mixin that wins the normal ``SessionMixin``
MRO) re-checks the cached verdict's ``expires_at`` on every call, so a credential
that expires inside the short verify TTL fails closed even on a cache hit. The
``LifecycleMixin`` copy is shadowed in the composed manager but is still reachable
through an MRO reorder or a direct/unbound caller. It caches its *whole*
``verify_token`` and previously had no such re-check: with ``token_binding`` and
the verify rate limit both disabled (the only config where its cache is active),
a cached-but-expired verdict kept authenticating until eviction.

These tests call the mirror *unbound* with a host that only inherits
``LifecycleMixin``, so they exercise the mirror's own cache and deny path rather
than the active ``VerificationMixin`` implementation.
"""

from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenExpiredError
from src.core.auth.managers.session.lifecycle import LifecycleMixin
# Importing the active verifier registers TokenInfo for cache reconstruction,
# so a hit yields a real TokenInfo (with .expires_at) rather than a plain dict.
from src.core.auth.managers.session.verification import VerificationMixin  # noqa: F401
from src.core.auth.models import AccountType, TokenInfo
from src.core.database import invalidate_pattern

# Structurally valid session credential; only its digest reaches a cache key.
_TOKEN = "123456789." + "a" * 43
_PARSED = {"token_type": "session", "id": 123456789, "secret": "a" * 43}
_FUTURE_EXPIRY_MS = 4102444800000  # year 2100: valid when the cache is warmed


def _info(expires_at=_FUTURE_EXPIRY_MS):
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


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    invalidate_pattern("token_verify:*")
    yield
    invalidate_pattern("token_verify:*")


class _MirrorHost(LifecycleMixin):
    """Host that resolves every verifier attribute to the LifecycleMixin mirror."""

    def __init__(self):
        # binding + rate limiting off -> the mirror's cache is actually enabled.
        self._config = {"security": {}, "sessions": {}}
        self._db = object()


def test_mirror_denies_cached_but_expired_verdict():
    """A cached verdict that has since expired must fail closed on the hit."""
    host = _MirrorHost()
    info = _info()
    calls = {"verify": 0}

    def fake_verify(*args, **kwargs):
        calls["verify"] += 1
        return info

    with (
        patch(
            "src.core.auth.managers.session.lifecycle.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            LifecycleMixin, "_verify_session_token", side_effect=fake_verify
        ),
    ):
        # Warm the mirror's cache with a still-valid verdict.
        first = LifecycleMixin.verify_token(host, _TOKEN)
        assert first.valid is True
        assert getattr(first, "expires_at", None) == _FUTURE_EXPIRY_MS
        assert calls["verify"] == 1

        # The credential expires inside the 10s cache TTL. Force the verifier's
        # clock-sensitive check to report it expired.
        with patch(
            "src.core.auth.managers.session.lifecycle.is_expired",
            return_value=True,
        ):
            with pytest.raises(TokenExpiredError):
                LifecycleMixin.verify_token(host, _TOKEN)

        # The denial came from the cache-hit re-check, not a fresh DB verify.
        assert calls["verify"] == 1


def test_mirror_cache_still_serves_unexpired_verdict():
    """Control: the re-check must not turn every cache hit into a denial."""
    host = _MirrorHost()
    info = _info()
    calls = {"verify": 0}

    def fake_verify(*args, **kwargs):
        calls["verify"] += 1
        return info

    with (
        patch(
            "src.core.auth.managers.session.lifecycle.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            LifecycleMixin, "_verify_session_token", side_effect=fake_verify
        ),
    ):
        assert LifecycleMixin.verify_token(host, _TOKEN).valid is True
        assert LifecycleMixin.verify_token(host, _TOKEN).valid is True
        assert calls["verify"] == 1  # second call hit the cache
