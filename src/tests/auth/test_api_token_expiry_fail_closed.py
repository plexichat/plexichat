"""A persisted API-token expiry must be revalidated when the verdict is restored.

``verify_api_access_token`` restores the whole verdict (including the stored
``expires_at``) from the token row and, on a cache hit, from the verification
cache.  The expiry gate used a truthiness check::

    if expires_at and int(expires_at) <= now:
        deny

A corrupt / partially-migrated row whose ``expires_at`` is ``0`` (or an empty
string) is therefore read as "no expiry" and authenticates forever, and an
absurdly future-dated value is honoured as a valid credential.  Both are the
same fail-open shape as an expired credential being revived from persisted
state.  Revalidate with ``is_expired`` (which fails closed on zero, malformed
and out-of-range values) while preserving ``NULL`` as "never expires".

The verifier is exercised directly with a stubbed introspection that stands in
for the persisted row; this keeps the test independent of the shared migrated
DB template while still driving the real ``verify_api_access_token`` gate.
"""

import time

import pytest

from src.core.auth.managers.api_token import (
    ApiTokenMixin,
    _invalidate_api_token_verify_cache,
)
from src.core.auth.tokens import MAX_EXPIRY_MS

pytestmark = [pytest.mark.auth]


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    _invalidate_api_token_verify_cache()
    yield
    _invalidate_api_token_verify_cache()


class _Verifier:
    """Bind the real verifier with a controlled 'persisted' expiry field."""

    verify_api_access_token = ApiTokenMixin.verify_api_access_token

    def __init__(self, expires_at):
        self._expires_at = expires_at
        self.denied_reasons = []

    def _get_timestamp(self):
        return int(time.time() * 1000)

    def _introspect_api_access_token(self, token, ip_address):
        return {
            "authentic": True,
            "token_id": 42,
            "expires_at": self._expires_at,
            "scope_mode": "none",
            "scope_match": True,
            "has_scopes": False,
            "last_used_at": 0,
            "first_used_at": None,
        }

    def _record_api_access_token_event(self, **kwargs):
        self.denied_reasons.append(kwargs.get("reject_reason"))

    def _ua_index(self, user_agent):
        return "ua-index"

    def _encrypt_ua(self, user_agent, context):
        return "encrypted-ua"

    class _Crypto:
        def fast_blind_index(self, value, purpose):
            return "blind-index"

        def encrypt_data(self, value, context=None):
            return "encrypted"

    crypto = _Crypto()

    class _DB:
        def execute(self, *args, **kwargs):
            return None

    _db = _DB()


def _verify(expires_at, token="test-credential"):
    verifier = _Verifier(expires_at)
    allowed = verifier.verify_api_access_token(
        token, ip_address="203.0.113.44", user_agent="expiry-revalidation/1.0"
    )
    return allowed, verifier.denied_reasons


def test_zero_expiry_from_persisted_row_denies():
    """A falsy corrupt expiry must not be treated as 'never expires'."""
    allowed, reasons = _verify(0, "zero-expiry")
    assert allowed is False
    assert "expired" in reasons


def test_empty_expiry_from_persisted_row_denies():
    """An empty-string expiry is malformed, not an unbounded credential."""
    assert _verify("", "empty-expiry")[0] is False


def test_far_future_expiry_from_persisted_row_denies():
    """An out-of-range forged expiry cannot grant a permanent credential."""
    assert _verify(MAX_EXPIRY_MS + 1000, "future-expiry")[0] is False


def test_past_expiry_from_persisted_row_denies():
    """Control: an ordinary expired value is still denied."""
    assert _verify(int(time.time() * 1000) - 1000, "past-expiry")[0] is False


def test_valid_future_expiry_still_authenticates():
    """Control: the revalidation does not reject a healthy token."""
    assert _verify(int(time.time() * 1000) + 600_000, "live-expiry")[0] is True


def test_null_expiry_still_authenticates():
    """Control: NULL genuinely means 'never expires'."""
    assert _verify(None, "eternal-expiry")[0] is True
