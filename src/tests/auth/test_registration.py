"""
Registration tests for auth module.
"""

import pytest


class TestRegistration:
    """Test user registration."""

    def test_register_valid_user(self, db_and_auth):
        """Test registering a valid user."""
        db, auth = db_and_auth

        user = auth.register(
            username="alice",
            email="alice@example.com",
            password="SecurePass123!"
        )

        assert user is not None
        assert user.username == "alice"
        assert user.email == "alice@example.com"
        assert user.id > 0

    def test_register_duplicate_username(self, db_and_auth):
        """Test that duplicate username fails."""
        db, auth = db_and_auth

        auth.register("bob", "bob@example.com", "SecurePass123!")

        with pytest.raises(auth.UserExistsError) as exc:
            auth.register("bob", "bob2@example.com", "SecurePass123!")

        assert exc.value.field == "username"

    def test_register_duplicate_email(self, db_and_auth):
        """Test that duplicate email fails."""
        db, auth = db_and_auth

        auth.register("charlie", "charlie@example.com", "SecurePass123!")

        with pytest.raises(auth.UserExistsError) as exc:
            auth.register("charlie2", "charlie@example.com", "SecurePass123!")

        assert exc.value.field == "email"

    def test_register_weak_password_too_short(self, db_and_auth):
        """Test that short password fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.WeakPasswordError) as exc:
            auth.register("dave", "dave@example.com", "Short1!")

        assert "at least" in str(exc.value)

    def test_register_weak_password_no_uppercase(self, db_and_auth):
        """Test that password without uppercase fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.WeakPasswordError) as exc:
            auth.register("eve", "eve@example.com", "lowercase123!")

        assert "uppercase" in str(exc.value).lower()

    def test_register_weak_password_no_lowercase(self, db_and_auth):
        """Test that password without lowercase fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.WeakPasswordError) as exc:
            auth.register("frank", "frank@example.com", "UPPERCASE123!")

        assert "lowercase" in str(exc.value).lower()

    def test_register_weak_password_no_digit(self, db_and_auth):
        """Test that password without digit fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.WeakPasswordError) as exc:
            auth.register("grace", "grace@example.com", "NoDigitsHere!")

        assert "digit" in str(exc.value).lower()

    def test_register_weak_password_no_special(self, db_and_auth):
        """Test that password without special char fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.WeakPasswordError) as exc:
            auth.register("henry", "henry@example.com", "NoSpecial123")

        assert "special" in str(exc.value).lower()

    def test_register_invalid_email(self, db_and_auth):
        """Test that invalid email fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.InvalidEmailError):
            auth.register("ivan", "not-an-email", "SecurePass123!")

    def test_register_invalid_username_too_short(self, db_and_auth):
        """Test that short username fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.InvalidUsernameError):
            auth.register("ab", "ab@example.com", "SecurePass123!")

    def test_register_invalid_username_special_chars(self, db_and_auth):
        """Test that username with special chars fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.InvalidUsernameError):
            auth.register("user@name", "user@example.com", "SecurePass123!")

    def test_register_reserved_username(self, db_and_auth):
        """Test that reserved username fails."""
        db, auth = db_and_auth

        with pytest.raises(auth.InvalidUsernameError):
            auth.register("admin", "admin@example.com", "SecurePass123!")

    def test_register_creates_default_permissions(self, db_and_auth):
        """Test that new user has default permissions."""
        db, auth = db_and_auth

        user = auth.register("julia", "julia@example.com", "SecurePass123!")

        assert user.permissions is not None
        assert user.permissions.get("messages.send") is True
        assert user.permissions.get("bots.create") is True

    def test_register_with_device_info(self, db_and_auth):
        """Test registration with device info."""
        db, auth = db_and_auth

        user = auth.register(
            username="kate",
            email="kate@example.com",
            password="SecurePass123!",
            device_info={"fingerprint": "abc123", "name": "Test Device"}
        )

        assert user is not None

        devices = auth.get_devices(user.id)
        assert len(devices) == 1
        assert devices[0].fingerprint == "abc123"

    def test_register_with_ip_address(self, db_and_auth):
        """Test registration tracks IP."""
        db, auth = db_and_auth

        user = auth.register(
            username="leo",
            email="leo@example.com",
            password="SecurePass123!",
            ip_address="192.168.1.1"
        )

        assert user is not None
        # IP tracking is internal, just verify no error
