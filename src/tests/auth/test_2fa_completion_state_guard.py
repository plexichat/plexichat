"""Regression: 2FA completion must re-check account state at redemption time.

A 2FA challenge lives for minutes. It used to be redeemable for a fresh
session after the account had been locked, frozen, had 2FA disabled, or had
not yet verified its email — the HTTP route patches this post-hoc, but the
manager API itself issued the session. These tests pin the manager-level
guard and the bounded challenge store.

Also covers that expired challenge rows are pruned instead of accumulating
without bound.
"""

from unittest.mock import patch

import pyotp
import pytest

from src.core.auth.exceptions import (
    AccountLockedError,
    EmailNotVerifiedError,
    TokenInvalidError,
)
from src.core.auth.managers.twofactor import TwoFactorMixin
from src.core.auth.models import AuthStatus
from src.utils import encryption


def _make_2fa_user(auth_manager, username):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(username, f"{username}@example.com", "TestPass123!")
    setup = auth_manager.setup_2fa(user.id)
    totp = pyotp.TOTP(setup.secret)
    auth_manager.confirm_2fa(user.id, totp.now())
    return user, totp


def _login_challenge(auth_manager, username):
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    assert result.status == AuthStatus.TWO_FACTOR_REQUIRED
    return result


def _insert_challenge(manager, user_id, expires_at):
    """Mint a real challenge via the manager, then age it to ``expires_at``."""
    challenge = manager._create_2fa_challenge(user_id, None, None, None)
    manager._db.execute(
        "UPDATE auth_2fa_challenges SET expires_at = ? WHERE id = ?",
        (expires_at, challenge.id),
    )
    return challenge.id, challenge.token


class TestCompletionStateGuard:
    def test_locked_account_cannot_redeem_challenge(self, auth_manager, db):
        user, totp = _make_2fa_user(auth_manager, "guard_locked_user")
        result = _login_challenge(auth_manager, "guard_locked_user")

        now = auth_manager._get_manager()._get_timestamp()
        db.execute(
            "UPDATE auth_users SET account_locked = 1, locked_until = ? WHERE id = ?",
            (now + 600000, user.id),
        )

        with pytest.raises(AccountLockedError):
            auth_manager.complete_2fa(result.challenge_token, totp.now())
        count = db.fetch_one(
            "SELECT COUNT(*) AS c FROM auth_sessions WHERE user_id = ?", (user.id,)
        )
        assert count["c"] == 0

    def test_frozen_account_cannot_redeem_challenge(self, auth_manager, db):
        user, totp = _make_2fa_user(auth_manager, "guard_frozen_user")
        result = _login_challenge(auth_manager, "guard_frozen_user")

        db.execute(
            "UPDATE auth_users SET deletion_status = 'frozen' WHERE id = ?", (user.id,)
        )
        with pytest.raises(AccountLockedError):
            auth_manager.complete_2fa(result.challenge_token, totp.now())
        count = db.fetch_one(
            "SELECT COUNT(*) AS c FROM auth_sessions WHERE user_id = ?", (user.id,)
        )
        assert count["c"] == 0

    def test_disabled_2fa_cannot_redeem_old_challenge(self, auth_manager, db):
        user, totp = _make_2fa_user(auth_manager, "guard_disabled_user")
        result = _login_challenge(auth_manager, "guard_disabled_user")

        with patch.object(encryption, "verify_password", return_value=True):
            assert auth_manager.disable_2fa(user.id, "TestPass123!", totp.now()) is True

        # Must fail closed (and never deep-raise decrypting a NULL secret).
        with pytest.raises(TokenInvalidError):
            auth_manager.complete_2fa(result.challenge_token, totp.now())
        count = db.fetch_one(
            "SELECT COUNT(*) AS c FROM auth_sessions WHERE user_id = ?", (user.id,)
        )
        assert count["c"] == 0

    def test_unverified_email_cannot_redeem_challenge_when_required(
        self, auth_manager, db
    ):
        manager = auth_manager._get_manager()
        old_accounts = manager._config.get("accounts", {})
        new_accounts = dict(old_accounts)
        new_accounts["require_email_verification"] = True
        manager._config["accounts"] = new_accounts
        try:
            user, totp = _make_2fa_user(auth_manager, "guard_unverified_user")
            # A direct challenge: login itself is gated by the email check.
            now = manager._get_timestamp()
            _cid, full_token = _insert_challenge(manager, user.id, now + 300000)
            with pytest.raises(EmailNotVerifiedError):
                auth_manager.complete_2fa(full_token, totp.now())
        finally:
            manager._config["accounts"] = old_accounts

    def test_expired_challenges_are_pruned_on_mint(self, auth_manager, db):
        manager = auth_manager._get_manager()
        saved_cleanup = TwoFactorMixin._last_challenge_cleanup
        TwoFactorMixin._last_challenge_cleanup = 0
        try:
            user, _totp = _make_2fa_user(auth_manager, "guard_prune_user")
            now = manager._get_timestamp()
            expired_ids = {
                _insert_challenge(manager, user.id, now - 1000)[0],
                _insert_challenge(manager, user.id, now - 5000)[0],
            }
            live_id = _insert_challenge(manager, user.id, now + 300000)[0]

            # Trigger the lazy prune on the next minted challenge.
            TwoFactorMixin._last_challenge_cleanup = 0
            _login_challenge(auth_manager, "guard_prune_user")

            rows = db.fetch_all(
                "SELECT id FROM auth_2fa_challenges WHERE user_id = ?", (user.id,)
            )
            remaining = {row["id"] for row in rows}
            assert live_id in remaining
            assert not (expired_ids & remaining)
            assert TwoFactorMixin._last_challenge_cleanup > 0
        finally:
            TwoFactorMixin._last_challenge_cleanup = saved_cleanup
