"""
Two-factor authentication tests for auth module.
"""

import pytest
import pyotp
from src.core.auth import AuthStatus
from src.core.auth.exceptions import (
    TwoFactorInvalidError,
    InvalidCredentialsError,
    TokenInvalidError,
)
from src.core.auth.tokens import parse_token
from src.core.auth.totp import get_totp_config
from unittest.mock import patch


class TestTwoFactorAuth:
    """Test 2FA functionality."""

    def test_setup_2fa_returns_secret(self, db, auth_manager):
        """Test 2FA setup returns secret."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "setup_secret_test",
                "setup_secret_test@example.com",
                "TestPass123!",
            )

        setup = auth_manager.setup_2fa(user.id)

        assert setup.secret is not None
        assert len(setup.secret) > 0

    def test_setup_2fa_returns_qr_uri(self, db, auth_manager):
        """Test 2FA setup returns QR URI."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "setup_qr_test",
                "setup_qr_test@example.com",
                "TestPass123!",
            )

        setup = auth_manager.setup_2fa(user.id)

        assert setup.qr_uri is not None
        assert setup.qr_uri.startswith("otpauth://totp/")

    def test_setup_2fa_returns_backup_codes(self, db, auth_manager):
        """Test 2FA setup returns backup codes."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "setup_backup_test",
                "setup_backup_test@example.com",
                "TestPass123!",
            )

        setup = auth_manager.setup_2fa(user.id)

        assert setup.backup_codes is not None
        expected_count = get_totp_config().get("backup_code_count", 10)
        assert len(setup.backup_codes) == expected_count
        for code in setup.backup_codes:
            assert "-" in code

    def test_confirm_2fa_with_valid_code(self, db, auth_manager):
        """Test confirming 2FA with valid code."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "confirm2fa_test1",
                "confirm2fa_test1@example.com",
                "TestPass123!",
            )
        setup = auth_manager.setup_2fa(user.id)

        totp = pyotp.TOTP(setup.secret)
        code = totp.now()

        result = auth_manager.confirm_2fa(user.id, code)
        assert result is True

        status = auth_manager.get_2fa_status(user.id)
        assert status.enabled is True

    def test_confirm_2fa_with_invalid_code(self, db, auth_manager):
        """Test confirming 2FA with invalid code fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "invalid2fa_test1",
                "invalid2fa_test1@example.com",
                "TestPass123!",
            )
        auth_manager.setup_2fa(user.id)

        with pytest.raises(TwoFactorInvalidError):
            auth_manager.confirm_2fa(user.id, "000000")

    def test_login_requires_2fa_when_enabled(self, db, auth_manager):
        """Test login requires 2FA when enabled."""
        from src.utils import encryption

        username = "require2fa_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username, f"{username}@example.com", "TestPass123!"
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")

        assert result.status == AuthStatus.TWO_FACTOR_REQUIRED
        assert result.challenge_token is not None
        assert "totp" in result.methods

    def test_complete_2fa_with_totp(self, db, auth_manager):
        """Test completing 2FA with TOTP code."""
        from src.utils import encryption

        username = "complete2fa_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username, f"{username}@example.com", "TestPass123!"
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")
        final = auth_manager.complete_2fa(result.challenge_token, totp.now())

        assert final.status == AuthStatus.SUCCESS
        assert final.token is not None

    def test_complete_2fa_rejects_tampered_challenge_hash(self, db, auth_manager):
        """Test 2FA completion rejects a challenge whose stored hash was tampered."""
        from src.utils import encryption

        username = "tampered2fa_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username, f"{username}@example.com", "TestPass123!"
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")
        parsed = parse_token(result.challenge_token)
        db.execute(
            "UPDATE auth_2fa_challenges SET token_hash = ? WHERE id = ?",
            ("tampered", parsed["id"]),
        )

        with pytest.raises(TokenInvalidError):
            auth_manager.complete_2fa(result.challenge_token, totp.now())

    def test_complete_2fa_with_backup_code(self, db, auth_manager):
        """Test completing 2FA with backup code."""
        from src.utils import encryption

        username = "backup2fa_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username, f"{username}@example.com", "TestPass123!"
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        backup_code = setup.backup_codes[0]

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login(username, "TestPass123!")
        final = auth_manager.complete_2fa(result.challenge_token, backup_code)

        assert final.status == AuthStatus.SUCCESS

    def test_backup_code_single_use(self, db, auth_manager):
        """Test backup code can only be used once."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "singleuse_test",
                "singleuse_test@example.com",
                "TestPass123!",
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        backup_code = setup.backup_codes[0]

        with patch.object(encryption, "verify_password", return_value=True):
            result1 = auth_manager.login("singleuse_test", "TestPass123!")
        auth_manager.complete_2fa(result1.challenge_token, backup_code)

        with patch.object(encryption, "verify_password", return_value=True):
            result2 = auth_manager.login("singleuse_test", "TestPass123!")

        with pytest.raises(TwoFactorInvalidError):
            auth_manager.complete_2fa(result2.challenge_token, backup_code)

    def test_disable_2fa(self, db, auth_manager):
        """Test disabling 2FA."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "disable2fa_test",
                "disable2fa_test@example.com",
                "TestPass123!",
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.disable_2fa(user.id, "TestPass123!", totp.now())
        assert result is True

        status = auth_manager.get_2fa_status(user.id)
        assert status.enabled is False

    def test_disable_2fa_wrong_password(self, db, auth_manager):
        """Test disabling 2FA with wrong password fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "wrongpwd2fa_test",
                "wrongpwd2fa_test@example.com",
                "TestPass123!",
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        with pytest.raises(InvalidCredentialsError):
            auth_manager.disable_2fa(user.id, "WrongPassword!", totp.now())

    def test_regenerate_backup_codes(self, db, auth_manager):
        """Test regenerating backup codes."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "regen2fa_test",
                "regen2fa_test@example.com",
                "TestPass123!",
            )
        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        old_codes = setup.backup_codes
        with patch.object(encryption, "verify_password", return_value=True):
            new_codes = auth_manager.regenerate_backup_codes(user.id, "TestPass123!")

        expected_count = get_totp_config().get("backup_code_count", 10)
        assert len(new_codes) == expected_count
        assert new_codes != old_codes

    def test_get_2fa_status(self, db, auth_manager):
        """Test getting 2FA status."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                "status2fa_test",
                "status2fa_test@example.com",
                "TestPass123!",
            )

        status = auth_manager.get_2fa_status(user.id)
        assert status.enabled is False

        setup = auth_manager.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth_manager.confirm_2fa(user.id, totp.now())

        status = auth_manager.get_2fa_status(user.id)
        assert status.enabled is True
        expected_count = get_totp_config().get("backup_code_count", 10)
        assert status.backup_codes_remaining == expected_count
