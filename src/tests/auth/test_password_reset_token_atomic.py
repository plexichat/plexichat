"""Regression tests for password-reset token single-use / expiry.

The reset-password claim must re-check every validity predicate inside the
atomic single-use UPDATE, not only in the preceding SELECT. Otherwise a token
that expires between the validity read and the claim can still rotate the
credential (a time-of-check/time-of-use gap), and a corrupt expiry value can
be treated as "not expired".
"""

import time
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.tokens import create_email_token
from src.utils import encryption


def _make_reset_token(db, auth_manager, label="resetatomic"):
    """Register a user and insert a fresh, unexpired reset token for them."""
    unique = uuid.uuid4().hex[:10]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(username, email, "TestPass123!")

    token_id = int(uuid.uuid4().hex[:15], 16) or 1
    full_token, token_hash = create_email_token(token_id)
    now = int(time.time() * 1000)
    db.execute(
        "INSERT INTO auth_email_tokens "
        "(id, user_id, token_hash, token_type, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token_id, user.id, token_hash, "reset_password", now, now + 3600 * 1000),
    )
    original_hash = db.fetch_one(
        "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
    )["password_hash"]
    return user, full_token, token_id, original_hash


class TestPasswordResetTokenAtomic:
    def test_valid_reset_token_succeeds_once(self, db, auth_manager):
        """A valid token rotates the credential and is consumed on first use."""
        user, full_token, _, original_hash = _make_reset_token(db, auth_manager)

        assert auth_manager.reset_password(full_token, "BrandNewPass123!") is True

        with pytest.raises(TokenInvalidError):
            auth_manager.reset_password(full_token, "AnotherPass123!")

        row = db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
        )
        assert row["password_hash"] != original_hash

    def test_expiry_rechecked_at_atomic_claim(self, db, auth_manager):
        """A token expiring between the validity SELECT and the claim is denied.

        This reproduces the TOCTOU: the row is valid when reset_password reads
        it, but its expiry is moved into the past just before the single-use
        UPDATE executes. The authoritative claim must re-check expiry so the
        now-expired token cannot rotate the password.
        """
        user, full_token, token_id, original_hash = _make_reset_token(
            db, auth_manager, "resetexpiry"
        )
        manager = auth_manager._get_manager()
        real_execute = manager._db.execute

        def _expire_before_claim(sql, params=()):
            if "UPDATE auth_email_tokens SET used = 1" in sql:
                real_execute(
                    "UPDATE auth_email_tokens SET expires_at = ? WHERE id = ?",
                    (int(time.time() * 1000) - 1000, token_id),
                )
            return real_execute(sql, params)

        with patch.object(manager._db, "execute", side_effect=_expire_before_claim):
            with pytest.raises(TokenInvalidError):
                auth_manager.reset_password(full_token, "BrandNewPass123!")

        row = db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
        )
        assert row["password_hash"] == original_hash

    def test_malformed_expiry_fails_closed(self, db, auth_manager):
        """A seconds-scale (wrong-unit) expiry must not behave as valid."""
        user, full_token, token_id, original_hash = _make_reset_token(
            db, auth_manager, "resetmal"
        )
        db.execute(
            "UPDATE auth_email_tokens SET expires_at = ? WHERE id = ?",
            (int(time.time()), token_id),
        )

        with pytest.raises(TokenInvalidError):
            auth_manager.reset_password(full_token, "BrandNewPass123!")

        row = db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
        )
        assert row["password_hash"] == original_hash
