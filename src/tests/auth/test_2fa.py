"""
Two-factor authentication tests for auth module.
"""

import pytest
import pyotp


class TestTwoFactorAuth:
    """Test 2FA functionality."""

    def test_setup_2fa_returns_secret(self, registered_user):
        """Test 2FA setup returns secret."""
        user, auth, username = registered_user

        setup = auth.setup_2fa(user.id)

        assert setup.secret is not None
        assert len(setup.secret) > 0

    def test_setup_2fa_returns_qr_uri(self, registered_user):
        """Test 2FA setup returns QR URI."""
        user, auth, username = registered_user

        setup = auth.setup_2fa(user.id)

        assert setup.qr_uri is not None
        assert setup.qr_uri.startswith("otpauth://totp/")

    def test_setup_2fa_returns_backup_codes(self, registered_user):
        """Test 2FA setup returns backup codes."""
        user, auth, username = registered_user

        setup = auth.setup_2fa(user.id)

        assert setup.backup_codes is not None
        assert len(setup.backup_codes) == 5
        for code in setup.backup_codes:
            assert "-" in code

    def test_confirm_2fa_with_valid_code(self, db_and_auth):
        """Test confirming 2FA with valid code."""
        db, auth = db_and_auth

        user = auth.register("confirm2fa", "confirm2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)

        totp = pyotp.TOTP(setup.secret)
        code = totp.now()

        result = auth.confirm_2fa(user.id, code)
        assert result is True

        status = auth.get_2fa_status(user.id)
        assert status.enabled is True

    def test_confirm_2fa_with_invalid_code(self, db_and_auth):
        """Test confirming 2FA with invalid code fails."""
        db, auth = db_and_auth

        user = auth.register("invalid2fa", "invalid2fa@example.com", "TestPass123!")
        auth.setup_2fa(user.id)

        with pytest.raises(auth.TwoFactorInvalidError):
            auth.confirm_2fa(user.id, "000000")

    def test_login_requires_2fa_when_enabled(self, db_and_auth):
        """Test login requires 2FA when enabled."""
        db, auth = db_and_auth

        user = auth.register("require2fa", "require2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        result = auth.login("require2fa", "TestPass123!")

        assert result.status == auth.AuthStatus.TWO_FACTOR_REQUIRED
        assert result.challenge_token is not None
        assert "totp" in result.methods

    def test_complete_2fa_with_totp(self, db_and_auth):
        """Test completing 2FA with TOTP code."""
        db, auth = db_and_auth

        user = auth.register("complete2fa", "complete2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        result = auth.login("complete2fa", "TestPass123!")
        final = auth.complete_2fa(result.challenge_token, totp.now())

        assert final.status == auth.AuthStatus.SUCCESS
        assert final.token is not None

    def test_complete_2fa_with_backup_code(self, db_and_auth):
        """Test completing 2FA with backup code."""
        db, auth = db_and_auth

        user = auth.register("backup2fa", "backup2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        backup_code = setup.backup_codes[0]

        result = auth.login("backup2fa", "TestPass123!")
        final = auth.complete_2fa(result.challenge_token, backup_code)

        assert final.status == auth.AuthStatus.SUCCESS

    def test_backup_code_single_use(self, db_and_auth):
        """Test backup code can only be used once."""
        db, auth = db_and_auth

        user = auth.register("singleuse", "singleuse@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        backup_code = setup.backup_codes[0]

        result1 = auth.login("singleuse", "TestPass123!")
        auth.complete_2fa(result1.challenge_token, backup_code)

        result2 = auth.login("singleuse", "TestPass123!")

        with pytest.raises(auth.TwoFactorInvalidError):
            auth.complete_2fa(result2.challenge_token, backup_code)

    def test_disable_2fa(self, db_and_auth):
        """Test disabling 2FA."""
        db, auth = db_and_auth

        user = auth.register("disable2fa", "disable2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        result = auth.disable_2fa(user.id, "TestPass123!", totp.now())
        assert result is True

        status = auth.get_2fa_status(user.id)
        assert status.enabled is False

    def test_disable_2fa_wrong_password(self, db_and_auth):
        """Test disabling 2FA with wrong password fails."""
        db, auth = db_and_auth

        user = auth.register("wrongpwd2fa", "wrongpwd2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        with pytest.raises(auth.InvalidCredentialsError):
            auth.disable_2fa(user.id, "WrongPassword!", totp.now())

    def test_regenerate_backup_codes(self, db_and_auth):
        """Test regenerating backup codes."""
        db, auth = db_and_auth

        user = auth.register("regen2fa", "regen2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        old_codes = setup.backup_codes
        new_codes = auth.regenerate_backup_codes(user.id, "TestPass123!")

        assert len(new_codes) == 5
        assert new_codes != old_codes

    def test_get_2fa_status(self, db_and_auth):
        """Test getting 2FA status."""
        db, auth = db_and_auth

        user = auth.register("status2fa", "status2fa@example.com", "TestPass123!")

        status = auth.get_2fa_status(user.id)
        assert status.enabled is False

        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())

        status = auth.get_2fa_status(user.id)
        assert status.enabled is True
        assert status.backup_codes_remaining == 5
