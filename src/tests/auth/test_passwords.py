"""
Password management tests for auth module.
"""

import pytest


class TestPasswords:
    """Test password management."""

    def test_validate_strong_password(self, db_and_auth):
        """Test validating a strong password."""
        db, auth = db_and_auth

        result = auth.validate_password("StrongPass123!")

        assert result.valid is True
        assert result.score >= 4
        assert len(result.issues) == 0

    def test_validate_weak_password_short(self, db_and_auth):
        """Test validating short password."""
        db, auth = db_and_auth

        result = auth.validate_password("Short1!")

        assert result.valid is False
        assert any("at least" in issue for issue in result.issues)

    def test_validate_weak_password_no_upper(self, db_and_auth):
        """Test validating password without uppercase."""
        db, auth = db_and_auth

        result = auth.validate_password("lowercase123!")

        assert result.valid is False
        assert any("uppercase" in issue.lower() for issue in result.issues)

    def test_validate_weak_password_no_lower(self, db_and_auth):
        """Test validating password without lowercase."""
        db, auth = db_and_auth

        result = auth.validate_password("UPPERCASE123!")

        assert result.valid is False
        assert any("lowercase" in issue.lower() for issue in result.issues)

    def test_validate_weak_password_no_digit(self, db_and_auth):
        """Test validating password without digit."""
        db, auth = db_and_auth

        result = auth.validate_password("NoDigitsHere!")

        assert result.valid is False
        assert any("digit" in issue.lower() for issue in result.issues)

    def test_validate_weak_password_no_special(self, db_and_auth):
        """Test validating password without special char."""
        db, auth = db_and_auth

        result = auth.validate_password("NoSpecial123")

        assert result.valid is False
        assert any("special" in issue.lower() for issue in result.issues)

    def test_change_password_success(self, db_and_auth):
        """Test changing password successfully."""
        db, auth = db_and_auth

        user = auth.register("changepwd", "changepwd@example.com", "TestPass123!")

        result = auth.change_password(user.id, "TestPass123!", "NewSecurePass456!")
        assert result is True

        with pytest.raises(auth.InvalidCredentialsError):
            auth.login("changepwd", "TestPass123!")

        login_result = auth.login("changepwd", "NewSecurePass456!")
        assert login_result.status == auth.AuthStatus.SUCCESS

    def test_change_password_wrong_old(self, db_and_auth):
        """Test changing password with wrong old password."""
        db, auth = db_and_auth

        user = auth.register("wrongold", "wrongold@example.com", "TestPass123!")

        with pytest.raises(auth.InvalidCredentialsError):
            auth.change_password(user.id, "WrongOldPass!", "NewPass123!")

    def test_change_password_weak_new(self, db_and_auth):
        """Test changing to weak password fails."""
        db, auth = db_and_auth

        user = auth.register("weaknew", "weaknew@example.com", "TestPass123!")

        with pytest.raises(auth.WeakPasswordError):
            auth.change_password(user.id, "TestPass123!", "weak")

    def test_password_score_increases_with_length(self, db_and_auth):
        """Test password score increases with length."""
        db, auth = db_and_auth

        short = auth.validate_password("Abcd1234!")  # 9 chars, meets min
        long = auth.validate_password("AbcdefghijklmnopQRST123!")  # 24 chars

        # Long password should have higher or equal score
        assert long.score >= short.score
