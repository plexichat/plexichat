"""
Password management tests for auth module.
"""

import pytest
from unittest.mock import patch


class TestPasswords:
    """Test password management."""

    def test_validate_strong_password(self, db, auth_manager):
        """Test validating a strong password."""
        result = auth_manager.validate_password("StrongPass123!")

        assert result.valid is True
        assert result.score >= 4
        assert len(result.issues) == 0

    def test_validate_weak_password_short(self, db, auth_manager):
        """Test validating short password."""
        result = auth_manager.validate_password("Short1!")

        assert result.valid is False
        assert any("at least" in issue for issue in result.issues)

    def test_validate_weak_password_no_upper(self, db, auth_manager):
        """Test validating password without uppercase."""
        result = auth_manager.validate_password("lowercase123!")

        assert result.valid is False
        assert any("uppercase" in issue.lower() for issue in result.issues)

    def test_validate_weak_password_no_lower(self, db, auth_manager):
        """Test validating password without lowercase."""
        result = auth_manager.validate_password("UPPERCASE123!")

        assert result.valid is False
        assert any("lowercase" in issue.lower() for issue in result.issues)

    def test_validate_weak_password_no_digit(self, db, auth_manager):
        """Test validating password without digit."""
        result = auth_manager.validate_password("NoDigitsHere!")

        assert result.valid is False
        assert any("digit" in issue.lower() for issue in result.issues)

    def test_validate_weak_password_no_special(self, db, auth_manager):
        """Test validating password without special char."""
        result = auth_manager.validate_password("NoSpecial123")

        assert result.valid is False
        assert any("special" in issue.lower() for issue in result.issues)

    def test_change_password_success(self, db, auth_manager):
        """Test successful password change."""
        from src.utils import encryption
        from src.core.auth.exceptions import InvalidCredentialsError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="chpass_test",
                email="chpass_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.change_password(
                user.id, "TestPass123!", "NewSecurePass456!"
            )
        assert result is True

        with patch.object(encryption, "verify_password", return_value=False):
            with pytest.raises(InvalidCredentialsError):
                auth_manager.login("chpass_test", "TestPass123!")

        with patch.object(encryption, "verify_password", return_value=True):
            login_result = auth_manager.login("chpass_test", "NewSecurePass456!")
        assert login_result.status == auth_manager.AuthStatus.SUCCESS

    def test_change_password_wrong_old(self, db, auth_manager):
        """Test changing password with wrong old password."""
        from src.utils import encryption
        from src.core.auth.exceptions import InvalidCredentialsError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="wrongold_test",
                email="wrongold_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=False):
            with pytest.raises(InvalidCredentialsError):
                auth_manager.change_password(user.id, "WrongOldPass!", "NewPass123!")

    def test_change_password_weak_new(self, db, auth_manager):
        """Test changing to weak password fails."""
        from src.utils import encryption
        from src.core.auth.exceptions import WeakPasswordError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="weaknew_test",
                email="weaknew_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            with pytest.raises(WeakPasswordError):
                auth_manager.change_password(user.id, "TestPass123!", "weak")

    def test_password_score_increases_with_length(self, db, auth_manager):
        """Test password score increases with length."""
        short = auth_manager.validate_password("Abcd1234!")  # 9 chars, meets min
        long = auth_manager.validate_password("AbcdefghijklmnopQRST123!")  # 24 chars

        # Long password should have higher or equal score
        assert long.score >= short.score
