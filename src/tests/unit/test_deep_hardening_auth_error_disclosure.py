"""Regression tests for deep auth error/exception hardening.

Covers three disclosure classes:
  1. Secret-bearing auth dataclasses must not leak credentials via repr/str.
  2. Auth error messages must be bounded (no unbounded detail amplification).
  3. The mapped 429 Retry-After header must be a bounded integer (no header
     injection / unbounded backoff values).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes.exception_mapping import (
    RATE_LIMIT_RETRY_AFTER_SECONDS,
    _MAX_RETRY_AFTER_SECONDS,
    _safe_retry_after_seconds,
    raise_mapped,
)
from src.core.auth.exceptions import (
    MAX_AUTH_ERROR_MESSAGE_LENGTH,
    AuthError,
    WeakPasswordError,
)
from src.core.auth.models import AuthResult, AuthStatus, TwoFactorSetup
from src.core.servers import exceptions as srv

pytestmark = pytest.mark.unit


def test_auth_result_token_and_challenge_not_in_repr_or_str():
    result = AuthResult(
        status=AuthStatus.SUCCESS,
        token="sess_SUPERSECRET_VALUE",
        challenge_token="chal_SUPERSECRET_VALUE",
    )
    for rendered in (repr(result), str(result)):
        assert "sess_SUPERSECRET_VALUE" not in rendered
        assert "chal_SUPERSECRET_VALUE" not in rendered
    # Non-secret fields stay visible for diagnostics.
    assert "AuthResult" in repr(result)
    assert "status" in repr(result)


def test_two_factor_setup_secrets_not_in_repr_or_str():
    setup = TwoFactorSetup(
        secret="JBSWY3DPEHPK3PXPSEED",
        qr_uri="otpauth://totp/PlexiChat:user?secret=JBSWY3DPEHPK3PXPSEED",
        backup_codes=["ABCD-1234", "WXYZ-5678"],
        issuer="PlexiChat",
        username="user",
    )
    for rendered in (repr(setup), str(setup)):
        assert "JBSWY3DPEHPK3PXPSEED" not in rendered
        assert "otpauth://" not in rendered
        assert "ABCD-1234" not in rendered
        assert "WXYZ-5678" not in rendered
    assert "issuer" in repr(setup)
    assert "username" in repr(setup)


def test_auth_error_message_is_bounded():
    oversized = "A" * (MAX_AUTH_ERROR_MESSAGE_LENGTH * 3)
    assert len(str(AuthError(oversized))) == MAX_AUTH_ERROR_MESSAGE_LENGTH

    # Derived exception types go through the same bounded base.
    assert len(str(WeakPasswordError(oversized, []))) == MAX_AUTH_ERROR_MESSAGE_LENGTH

    # Ordinary-length messages are untouched.
    assert str(AuthError("Invalid credentials")) == "Invalid credentials"


def test_rate_limited_retry_after_header_is_bounded_integer():
    exc = srv.InviteRateLimitedError("slow down", retry_after_seconds=10**9)
    with pytest.raises(HTTPException) as ei:
        raise_mapped(exc)
    headers = ei.value.headers or {}
    value = headers["Retry-After"]
    assert value.isdigit(), value
    assert int(value) <= _MAX_RETRY_AFTER_SECONDS


def test_rate_limited_retry_after_rejects_invalid_and_negative_values():
    # CRLF-bearing string must never reach the header: fall back to constant.
    injected = srv.InviteRateLimitedError(
        "slow down", retry_after_seconds="60\r\nX-Injected: 1"  # type: ignore[arg-type]
    )
    assert _safe_retry_after_seconds(injected) == RATE_LIMIT_RETRY_AFTER_SECONDS

    negative = srv.InviteRateLimitedError("slow down", retry_after_seconds=-5)
    assert _safe_retry_after_seconds(negative) == 0

    # Webhook RateLimitError has no attribute -> documented fallback.
    from src.core.webhooks.exceptions import RateLimitError as WhRateLimitError

    assert _safe_retry_after_seconds(WhRateLimitError("slow")) == (
        RATE_LIMIT_RETRY_AFTER_SECONDS
    )
