import pytest
import utils.config as config
from src.core.auth.exceptions import AuthError
from unittest.mock import patch


@pytest.mark.auth
class TestAgeGate:
    """Tests for Age Gate functionality."""

    @pytest.fixture
    def auth_with_age_gate(self, db):
        """Auth manager with boolean age gate enabled."""
        from src.core.auth.manager import AuthManager

        # Override config
        old_config = config.get("authentication", {})
        new_config = old_config.copy()
        new_config["accounts"] = old_config.get("accounts", {}).copy()
        new_config["accounts"]["age_gate_enabled"] = True
        new_config["accounts"]["minimum_age"] = 13
        new_config["accounts"]["age_verification_type"] = "boolean"

        config.set("authentication", new_config)

        manager = AuthManager(db)
        yield manager

        # Restore config
        config.set("authentication", old_config)

    @pytest.fixture
    def auth_with_dob_gate(self, db):
        """Auth manager with DOB age gate enabled."""
        from src.core.auth.manager import AuthManager

        # Override config
        old_config = config.get("authentication", {})
        new_config = old_config.copy()
        new_config["accounts"] = old_config.get("accounts", {}).copy()
        new_config["accounts"]["age_gate_enabled"] = True
        new_config["accounts"]["minimum_age"] = 18
        new_config["accounts"]["age_verification_type"] = "dob"

        config.set("authentication", new_config)

        manager = AuthManager(db)
        yield manager

        # Restore config
        config.set("authentication", old_config)

    def test_boolean_mode_success(self, auth_with_age_gate):
        """Test registration success with boolean age verification."""
        from src.utils import encryption

        username = "user_bool_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_with_age_gate.register(
                username=username,
                email=f"{username}@example.com",
                password="SecurePassword123!",
                age=15,
            )
        assert user.age_verified is True
        assert user.date_of_birth is None

    def test_boolean_mode_underage(self, auth_with_age_gate):
        """Test registration failure for underage user in boolean mode."""
        from src.utils import encryption

        username = "user_young_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(AuthError) as exc:
                auth_with_age_gate.register(
                    username=username,
                    email=f"{username}@example.com",
                    password="SecurePassword123!",
                    age=10,
                )
        assert "Minimum age" in str(exc.value)

    def test_boolean_mode_missing_age(self, auth_with_age_gate):
        """Test registration failure when age is missing in boolean mode."""
        from src.utils import encryption

        username = "user_noage_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(AuthError) as exc:
                auth_with_age_gate.register(
                    username=username,
                    email=f"{username}@example.com",
                    password="SecurePassword123!",
                    # No age provided
                )
        assert "Age is required" in str(exc.value)

    def test_dob_mode_success(self, auth_with_dob_gate):
        """Test registration success with DOB verification and encrypted storage."""
        from src.utils import encryption

        username = "user_dob_test1"
        # User is 23 years old (assuming current year 2026 per context)
        # 2026 - 2003 = 23 > 18
        dob = "2003-05-20"

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_with_dob_gate.register(
                username=username,
                email=f"{username}@example.com",
                password="SecurePassword123!",
                dob=dob,
            )
        assert user.age_verified is True
        # DOB should be available on the user object (manager decrypts it)
        assert user.date_of_birth == dob

        # Verify it is encrypted in DB
        db = auth_with_dob_gate._db
        row = db.fetch_one(
            "SELECT date_of_birth FROM auth_users WHERE id = ?", (user.id,)
        )
        assert row["date_of_birth"] != dob
        assert "ENC:" in row["date_of_birth"]  # Assuming standard encryption format

    def test_dob_mode_underage(self, auth_with_dob_gate):
        """Test registration failure for underage user in DOB mode."""
        from src.utils import encryption

        username = "user_dob_young_test1"
        # User is 10 years old (2026 - 2016 = 10 < 18)
        dob = "2016-01-01"

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(AuthError) as exc:
                auth_with_dob_gate.register(
                    username=username,
                    email=f"{username}@example.com",
                    password="SecurePassword123!",
                    dob=dob,
                )
        assert "Minimum age" in str(exc.value)

    def test_dob_mode_invalid_format(self, auth_with_dob_gate):
        """Test registration failure with invalid DOB format."""
        from src.utils import encryption

        username = "user_bad_dob_test1"

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            with pytest.raises(AuthError) as exc:
                auth_with_dob_gate.register(
                    username=username,
                    email=f"{username}@example.com",
                    password="SecurePassword123!",
                    dob="20-05-2000",  # Wrong format
                )
        assert "Invalid date format" in str(exc.value)
