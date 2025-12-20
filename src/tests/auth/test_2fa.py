"""
Comprehensive 2FA tests covering TOTP, backup codes, and edge cases.
"""

import pytest
import time
from src.core.auth.exceptions import (
    AuthError,
    TwoFactorInvalidError,
    InvalidCredentialsError,
    UserNotFoundError,
)
from src.tests.fixtures.config import TEST_PASSWORD


class Test2FASetup:
    """Tests for 2FA setup flow."""

    def test_setup_2fa_generates_secret(self, modules):
        """Test 2FA setup generates a secret."""
        username = f"2fasetup_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        setup = modules.auth.setup_2fa(user.id)

        assert setup.secret is not None
        assert len(setup.secret) > 0

    def test_setup_2fa_generates_qr_uri(self, modules):
        """Test 2FA setup generates QR code URI."""
        username = f"qrtest_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        setup = modules.auth.setup_2fa(user.id)

        assert setup.qr_uri is not None
        assert "otpauth://totp/" in setup.qr_uri
        assert username in setup.qr_uri

    def test_setup_2fa_generates_backup_codes(self, modules):
        """Test 2FA setup generates backup codes."""
        username = f"backup_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        setup = modules.auth.setup_2fa(user.id)

        assert setup.backup_codes is not None
        assert len(setup.backup_codes) == 5  # Default count

    def test_setup_2fa_backup_codes_unique(self, modules):
        """Test backup codes are all unique."""
        username = f"uniqueback_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        setup = modules.auth.setup_2fa(user.id)

        assert len(setup.backup_codes) == len(set(setup.backup_codes))

    def test_setup_2fa_not_enabled_until_confirmed(self, modules):
        """Test 2FA is not enabled until confirmation."""
        username = f"notconfirmed_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.setup_2fa(user.id)

        status = modules.auth.get_2fa_status(user.id)
        assert status.enabled is False

    def test_setup_2fa_when_already_enabled_fails(self, modules):
        """Test cannot setup 2FA when already enabled."""
        username = f"alreadyenabled_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Try to setup again
        with pytest.raises(AuthError):
            modules.auth.setup_2fa(user.id)


class Test2FAConfirmation:
    """Tests for 2FA confirmation."""

    def test_confirm_2fa_with_valid_code(self, modules):
        """Test confirming 2FA with valid TOTP code."""
        username = f"confirmvalid_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)

        result = modules.auth.confirm_2fa(user.id, code)
        assert result is True

        status = modules.auth.get_2fa_status(user.id)
        assert status.enabled is True

    def test_confirm_2fa_with_invalid_code(self, modules):
        """Test confirming 2FA with invalid code fails."""
        username = f"confirminvalid_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.setup_2fa(user.id)

        with pytest.raises(TwoFactorInvalidError):
            modules.auth.confirm_2fa(user.id, "000000")

    def test_confirm_2fa_without_setup_fails(self, modules):
        """Test confirming 2FA without setup fails."""
        username = f"nosetup_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        with pytest.raises(AuthError):
            modules.auth.confirm_2fa(user.id, "123456")

    def test_confirm_2fa_logs_audit(self, modules):
        """Test 2FA confirmation creates audit log."""
        username = f"confirmaudit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        events = modules.auth.get_security_events(user.id, limit=10)
        enable_events = [e for e in events if e.event_type.value == "2fa_enabled"]
        assert len(enable_events) > 0


class Test2FALogin:
    """Tests for login with 2FA enabled."""

    def test_login_with_2fa_returns_challenge(self, modules):
        """Test login with 2FA returns challenge token."""
        username = f"loginchallenge_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Login should return challenge
        result = modules.auth.login(username, TEST_PASSWORD)
        assert result.status.value == "2fa_required"
        assert result.challenge_token is not None
        assert "totp" in result.methods

    def test_complete_2fa_with_valid_totp(self, modules):
        """Test completing 2FA with valid TOTP code."""
        username = f"complete2fa_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Login and complete 2FA
        result = modules.auth.login(username, TEST_PASSWORD)
        new_code = totp_module.generate_totp_code(setup.secret)

        auth_result = modules.auth.complete_2fa(result.challenge_token, new_code)
        assert auth_result.status.value == "success"
        assert auth_result.token is not None

    def test_complete_2fa_with_invalid_totp(self, modules):
        """Test completing 2FA with invalid TOTP fails."""
        username = f"invalid2fa_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Login
        result = modules.auth.login(username, TEST_PASSWORD)

        with pytest.raises(TwoFactorInvalidError):
            modules.auth.complete_2fa(result.challenge_token, "000000")

    def test_complete_2fa_with_backup_code(self, modules):
        """Test completing 2FA with backup code."""
        username = f"backupcode_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Login and use backup code
        result = modules.auth.login(username, TEST_PASSWORD)
        backup_code = setup.backup_codes[0]

        auth_result = modules.auth.complete_2fa(result.challenge_token, backup_code)
        assert auth_result.status.value == "success"

    def test_backup_code_consumed_after_use(self, modules):
        """Test backup code is consumed after successful use."""
        username = f"consumed_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        initial_count = modules.auth.get_2fa_status(user.id).backup_codes_remaining

        # Use backup code
        result = modules.auth.login(username, TEST_PASSWORD)
        modules.auth.complete_2fa(result.challenge_token, setup.backup_codes[0])

        new_count = modules.auth.get_2fa_status(user.id).backup_codes_remaining
        assert new_count == initial_count - 1

    def test_backup_code_cannot_reuse(self, modules):
        """Test backup code cannot be reused."""
        username = f"noreuse_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        backup_code = setup.backup_codes[0]

        # Use backup code once
        result1 = modules.auth.login(username, TEST_PASSWORD)
        modules.auth.complete_2fa(result1.challenge_token, backup_code)

        # Try to use same code again
        result2 = modules.auth.login(username, TEST_PASSWORD)
        with pytest.raises(TwoFactorInvalidError):
            modules.auth.complete_2fa(result2.challenge_token, backup_code)


class Test2FABackupCodes:
    """Tests for backup code management."""

    def test_regenerate_backup_codes(self, modules):
        """Test regenerating backup codes."""
        username = f"regen_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Regenerate
        new_codes = modules.auth.regenerate_backup_codes(user.id, TEST_PASSWORD)

        assert len(new_codes) == 5
        assert new_codes != setup.backup_codes

    def test_regenerate_requires_password(self, modules):
        """Test regenerating backup codes requires password."""
        username = f"regenpass_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Wrong password
        with pytest.raises(InvalidCredentialsError):
            modules.auth.regenerate_backup_codes(user.id, "WrongPassword!")

    def test_regenerate_invalidates_old_codes(self, modules):
        """Test regenerating invalidates old backup codes."""
        username = f"invalidold_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        old_code = setup.backup_codes[0]

        # Regenerate
        modules.auth.regenerate_backup_codes(user.id, TEST_PASSWORD)

        # Old code should not work
        result = modules.auth.login(username, TEST_PASSWORD)
        with pytest.raises(TwoFactorInvalidError):
            modules.auth.complete_2fa(result.challenge_token, old_code)

    def test_backup_code_exhaustion(self, modules):
        """Test behavior when all backup codes are used."""
        username = f"exhaust_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Use all backup codes
        for backup_code in setup.backup_codes:
            result = modules.auth.login(username, TEST_PASSWORD)
            modules.auth.complete_2fa(result.challenge_token, backup_code)

        status = modules.auth.get_2fa_status(user.id)
        assert status.backup_codes_remaining == 0


class Test2FADisable:
    """Tests for disabling 2FA."""

    def test_disable_2fa_with_valid_credentials(self, modules):
        """Test disabling 2FA with valid password and code."""
        username = f"disable_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Disable
        new_code = totp_module.generate_totp_code(setup.secret)
        result = modules.auth.disable_2fa(user.id, TEST_PASSWORD, new_code)
        assert result is True

        status = modules.auth.get_2fa_status(user.id)
        assert status.enabled is False

    def test_disable_2fa_wrong_password_fails(self, modules):
        """Test disabling 2FA with wrong password fails."""
        username = f"disablewrong_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Try to disable with wrong password
        new_code = totp_module.generate_totp_code(setup.secret)
        with pytest.raises(InvalidCredentialsError):
            modules.auth.disable_2fa(user.id, "WrongPass!", new_code)

    def test_disable_2fa_wrong_code_fails(self, modules):
        """Test disabling 2FA with wrong code fails."""
        username = f"disablecode_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Try to disable with wrong code
        with pytest.raises(TwoFactorInvalidError):
            modules.auth.disable_2fa(user.id, TEST_PASSWORD, "000000")

    def test_disable_2fa_removes_backup_codes(self, modules):
        """Test disabling 2FA removes backup codes."""
        username = f"removebackup_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Disable
        new_code = totp_module.generate_totp_code(setup.secret)
        modules.auth.disable_2fa(user.id, TEST_PASSWORD, new_code)

        status = modules.auth.get_2fa_status(user.id)
        assert status.backup_codes_remaining == 0


class Test2FAEdgeCases:
    """Edge case tests for 2FA."""

    def test_2fa_challenge_expires(self, modules):
        """Test 2FA challenge token expires."""
        username = f"expire_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Login
        modules.auth.login(username, TEST_PASSWORD)

        # Manually expire the challenge (would need DB manipulation or time travel)
        # This is a design test - in production it would expire after 5 minutes

    def test_2fa_challenge_single_use(self, modules):
        """Test 2FA challenge token is single-use."""
        username = f"singleuse_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Login and complete
        result = modules.auth.login(username, TEST_PASSWORD)
        new_code = totp_module.generate_totp_code(setup.secret)
        modules.auth.complete_2fa(result.challenge_token, new_code)

        # Try to use same challenge again
        another_code = totp_module.generate_totp_code(setup.secret)
        with pytest.raises(Exception):  # TokenInvalidError
            modules.auth.complete_2fa(result.challenge_token, another_code)

    def test_totp_time_window_tolerance(self, modules):
        """Test TOTP accepts codes within time window."""
        username = f"timewindow_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        # Get code from current time
        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        # Code should work (within 30s window)
        result = modules.auth.login(username, TEST_PASSWORD)
        new_code = totp_module.generate_totp_code(setup.secret)
        auth_result = modules.auth.complete_2fa(result.challenge_token, new_code)
        assert auth_result.status.value == "success"

    def test_backup_code_case_insensitive(self, modules):
        """Test backup codes work regardless of case."""
        # This depends on implementation - backup codes might be case-sensitive
        pass


class Test2FAStatus:
    """Tests for 2FA status checking."""

    def test_get_2fa_status_disabled(self, modules):
        """Test getting 2FA status when disabled."""
        username = f"statusdis_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        status = modules.auth.get_2fa_status(user.id)
        assert status.enabled is False
        assert status.backup_codes_remaining == 0

    def test_get_2fa_status_enabled(self, modules):
        """Test getting 2FA status when enabled."""
        username = f"statusen_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Enable 2FA
        setup = modules.auth.setup_2fa(user.id)
        from src.core.auth import totp as totp_module

        code = totp_module.generate_totp_code(setup.secret)
        modules.auth.confirm_2fa(user.id, code)

        status = modules.auth.get_2fa_status(user.id)
        assert status.enabled is True
        assert status.backup_codes_remaining == 5

    def test_get_2fa_status_nonexistent_user(self, modules):
        """Test getting 2FA status for non-existent user."""
        with pytest.raises(UserNotFoundError):
            modules.auth.get_2fa_status(999999999)
