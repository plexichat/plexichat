"""Password-reset expiry must be re-enforced at the consumption write.

The validity SELECT in ``reset_password`` is only a fast-fail check. The
authoritative single-use decision is the guarded ``UPDATE ... WHERE used = 0
AND expires_at > ?``. That predicate must be evaluated against a clock read
at the moment of the write, not the ``now`` snapshot captured for the earlier
validity check: a token that expires between validation and the claim must
fail closed.

This mirrors the invite-redemption (cac562c8) and email-verification (ff89f82d)
TOCTOU fixes. Before the fix, the claim reused the validation ``now``, so a
two-value clock (validation sees live, write sees expired) wrongly consumed
the token and rotated the credential after expiry.
"""

import time
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.tokens import create_email_token
from src.utils import encryption


def _make_reset_token(db, auth_manager, expires_at):
    """Register a user and insert a reset token with a controlled expiry."""
    unique = uuid.uuid4().hex[:10]
    username = f"rste_{unique}"
    email = f"rste_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(username, email, "TestPass123!")

    token_id = int(uuid.uuid4().hex[:15], 16) or 1
    full_token, token_hash = create_email_token(token_id)
    db.execute(
        "INSERT INTO auth_email_tokens "
        "(id, user_id, token_hash, token_type, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token_id, user.id, token_hash, "reset_password", expires_at - 60_000, expires_at),
    )
    original_hash = db.fetch_one(
        "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
    )["password_hash"]
    return user, full_token, token_id, original_hash


class _TwoValueClock:
    """First clock read = validation time; all later reads = write time."""

    def __init__(self, validation_now, write_now):
        self.validation_now = validation_now
        self.write_now = write_now
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.validation_now if self.calls == 1 else self.write_now


def test_expiry_rechecked_at_consumption_write(db, auth_manager):
    """A token live at validation but expired at the write is not consumed."""
    manager = auth_manager._get_manager()
    expires_at = int(time.time() * 1000) + 60_000
    user, full_token, token_id, original_hash = _make_reset_token(
        db, auth_manager, expires_at
    )

    # Validation sees the token live (now < expiry); the write-time read sees
    # it expired (now > expiry). A claim that reuses the stale validation
    # clock would wrongly pass `expires_at > ?` and rotate the credential.
    clock = _TwoValueClock(expires_at - 1, expires_at + 1)
    with patch.object(manager, "_get_timestamp", side_effect=clock):
        with pytest.raises(TokenInvalidError):
            auth_manager.reset_password(full_token, "BrandNewPass123!")

    # The write-time clock was actually consulted (non-vacuous).
    assert clock.calls >= 2
    # Fail closed: token unconsumed and credential unchanged.
    row = db.fetch_one("SELECT used FROM auth_email_tokens WHERE id = ?", (token_id,))
    assert row["used"] == 0
    current = db.fetch_one(
        "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
    )
    assert current["password_hash"] == original_hash


def test_live_token_still_consumes_with_fresh_write_clock(db, auth_manager):
    """Control: a token live at both validation and write is consumed.

    Guards against a vacuous fix that rejects every reset regardless of the
    clock values.
    """
    manager = auth_manager._get_manager()
    expires_at = int(time.time() * 1000) + 60_000
    user, full_token, token_id, original_hash = _make_reset_token(
        db, auth_manager, expires_at
    )

    clock = _TwoValueClock(expires_at - 1, expires_at - 1)
    with patch.object(manager, "_get_timestamp", side_effect=clock):
        assert auth_manager.reset_password(full_token, "BrandNewPass123!") is True

    row = db.fetch_one("SELECT used FROM auth_email_tokens WHERE id = ?", (token_id,))
    assert row["used"] == 1
    current = db.fetch_one(
        "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
    )
    assert current["password_hash"] != original_hash
