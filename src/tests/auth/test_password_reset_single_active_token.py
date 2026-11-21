"""Regression: password-reset links are single-active and are retired on any
credential change.

The verification-email flow keeps exactly one live token per user, but the
reset-password flow used to leave every issued link independently valid. An
older (stolen/intercepted) link could therefore still take over the account
after the victim redeemed a newer one, and a stale link survived a deliberate
password change.
"""

import re
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.utils import encryption


_TOKEN_RE = re.compile(r"Token \(fallback\):\s*(\S+)")


class _CapturingSender:
    """Email sender that records bodies so the minted tokens can be read."""

    def __init__(self):
        self.bodies = []

    def send(self, email, subject, body):
        self.bodies.append(body)
        return True

    def tokens(self):
        return [
            m.group(1) for body in self.bodies for m in [_TOKEN_RE.search(body)] if m
        ]


def _register(auth_manager):
    username = f"u_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(username, f"{username}@example.com", "TestPass123!")
    return username


def _set_sender(auth_manager, sender):
    # The module-level helpers proxy to the singleton manager instance; the
    # sender lives on that instance, not on the module.
    auth_manager._get_manager().email_sender = sender


def test_reissue_retires_earlier_reset_token(db, auth_manager):
    username = _register(auth_manager)
    sender = _CapturingSender()
    _set_sender(auth_manager, sender)
    email = f"{username}@example.com"

    assert auth_manager.request_password_reset(email) is True
    assert auth_manager.request_password_reset(email) is True

    tokens = sender.tokens()
    assert len(tokens) == 2, "expected two reset links to have been emailed"
    first, second = tokens
    first_id = int(first.split(".")[1])

    # The first link has been marked used...
    row = db.fetch_one("SELECT used FROM auth_email_tokens WHERE id = ?", (first_id,))
    assert row is not None and int(row["used"]) == 1

    # ...and can no longer reset the password.
    with pytest.raises(TokenInvalidError):
        auth_manager.reset_password(first, "Str0ng!NewPassword2026")

    # The newest link still works.
    assert auth_manager.reset_password(second, "Str0ng!NewPassword2026") is True


def test_password_change_retires_outstanding_reset_token(db, auth_manager):
    username = _register(auth_manager)
    sender = _CapturingSender()
    _set_sender(auth_manager, sender)
    email = f"{username}@example.com"

    assert auth_manager.request_password_reset(email) is True
    (token,) = sender.tokens()

    user = auth_manager.get_user_by_username(username)
    with patch.object(encryption, "verify_password", return_value=True):
        assert (
            auth_manager.change_password(user.id, "TestPass123!", "Changed!Pass2026")
            is True
        )

    # A reset link minted before the deliberate password change is dead.
    with pytest.raises(TokenInvalidError):
        auth_manager.reset_password(token, "Another!Pass2026")


def test_successful_reset_retires_sibling_tokens(db, auth_manager):
    """Tokens minted before the one-active invariant was enforced are retired
    too when any reset completes (defense in depth for legacy rows)."""
    username = _register(auth_manager)
    sender = _CapturingSender()
    _set_sender(auth_manager, sender)
    email = f"{username}@example.com"
    assert auth_manager.request_password_reset(email) is True
    (token,) = sender.tokens()

    # Simulate a second, still-live token issued by an older build: un-retire
    # the first and insert a fresh one for the same user.
    user = auth_manager.get_user_by_username(username)
    db.execute(
        "UPDATE auth_email_tokens SET used = 0 WHERE user_id = ? AND token_type = 'reset_password'",
        (user.id,),
    )
    sibling_id = int(token.split(".")[1]) + 1
    secret = "sibling-secret-value"
    from src.core.auth.tokens import hash_token

    now_ms = auth_manager._get_manager()._get_timestamp()
    db.execute(
        "INSERT INTO auth_email_tokens "
        "(id, user_id, token_hash, token_type, created_at, expires_at, used) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (
            sibling_id,
            user.id,
            hash_token(secret),
            "reset_password",
            now_ms,
            now_ms + 3600000,
        ),
    )
    sibling = f"email.{sibling_id}.{secret}"

    # Redeeming one link retires the other.
    assert auth_manager.reset_password(token, "Str0ng!NewPassword2026") is True
    assert (
        db.fetch_one("SELECT used FROM auth_email_tokens WHERE id = ?", (sibling_id,))[
            "used"
        ]
        == 1
    )
    with pytest.raises(TokenInvalidError):
        auth_manager.reset_password(sibling, "Another!Pass2026")
