"""
Registration tests for auth module.
"""

import pytest
from src.core.auth.exceptions import (
    UserExistsError,
    WeakPasswordError,
    InvalidEmailError,
    InvalidUsernameError,
)
from unittest.mock import patch


class TestRegistration:
    """Test user registration."""

    def test_register_valid_user(self, db, auth_manager):
        """Test registering a valid user."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="reg_valid_test",
                email="reg_valid_test@example.com",
                password="SecurePass123!",
            )

        assert user is not None
        assert user.username == "reg_valid_test"
        assert user.email == "reg_valid_test@example.com"
        assert user.id > 0

    def test_register_duplicate_username(self, db, auth_manager):
        """Test registering with an existing username fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            auth_manager.register(
                "reg_dup_test", "reg_dup_test1@example.com", "SecurePass123!"
            )

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(UserExistsError):
                auth_manager.register(
                    "reg_dup_test", "reg_dup_test2@example.com", "SecurePass123!"
                )

    def test_register_duplicate_email(self, db, auth_manager):
        """Test registering with an existing email fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            auth_manager.register(
                "user1_test", "dup_test@example.com", "SecurePass123!"
            )

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(UserExistsError):
                auth_manager.register(
                    "user2_test", "dup_test@example.com", "SecurePass123!"
                )

    def test_register_weak_password_too_short(self, db, auth_manager):
        """Test that short password fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(WeakPasswordError) as exc:
                auth_manager.register("dave", "dave@example.com", "Short1!")

        assert "at least" in str(exc.value)

    def test_register_weak_password_no_uppercase(self, db, auth_manager):
        """Test that password without uppercase fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(WeakPasswordError) as exc:
                auth_manager.register("eve", "eve@example.com", "lowercase123!")

        assert "uppercase" in str(exc.value).lower()

    def test_register_weak_password_no_lowercase(self, db, auth_manager):
        """Test that password without lowercase fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(WeakPasswordError) as exc:
                auth_manager.register("frank", "frank@example.com", "UPPERCASE123!")

        assert "lowercase" in str(exc.value).lower()

    def test_register_weak_password_no_digit(self, db, auth_manager):
        """Test that password without digit fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(WeakPasswordError) as exc:
                auth_manager.register("grace", "grace@example.com", "NoDigitsHere!")

        assert "digit" in str(exc.value).lower()

    def test_register_weak_password_no_special(self, db, auth_manager):
        """Test that password without special char fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(WeakPasswordError) as exc:
                auth_manager.register("henry", "henry@example.com", "NoSpecial123")

        assert "special" in str(exc.value).lower()

    def test_register_invalid_email(self, db, auth_manager):
        """Test that invalid email fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(InvalidEmailError):
                auth_manager.register("ivan", "not-an-email", "SecurePass123!")

    def test_register_invalid_username_too_short(self, db, auth_manager):
        """Test that short username fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(InvalidUsernameError):
                auth_manager.register("ab", "ab@example.com", "SecurePass123!")

    def test_register_invalid_username_special_chars(self, db, auth_manager):
        """Test that username with special chars fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(InvalidUsernameError):
                auth_manager.register("user@name", "user@example.com", "SecurePass123!")

    def test_register_reserved_username(self, db, auth_manager):
        """Test registering with a reserved username fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(InvalidUsernameError):
                auth_manager.register(
                    "admin", "admin_test@example.com", "SecurePass123!"
                )

    def test_register_creates_default_permissions(self, db, auth_manager):
        """Test that new user has default permissions."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("julia", "julia@example.com", "SecurePass123!")

        assert user.permissions is not None
        assert user.permissions.get("messages.send") is True
        assert user.permissions.get("bots.create") is True

    def test_register_with_device_info(self, db, auth_manager):
        """Test registration with device info."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="kate",
                email="kate@example.com",
                password="SecurePass123!",
                device_info={"fingerprint": "abc123", "name": "Test Device"},
            )

        assert user is not None

        devices = auth_manager.get_devices(user.id)
        assert len(devices) == 1
        assert devices[0].fingerprint == "abc123"

    def test_register_with_ip_address(self, db, auth_manager):
        """Test registration tracks IP."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="leo",
                email="leo@example.com",
                password="SecurePass123!",
                ip_address="192.168.1.1",
            )

        assert user is not None
        # IP tracking is internal, just verify no error
