
import pytest
import uuid
import utils.config as config
from src.core.auth.exceptions import AuthError
from src.utils.encryption import encrypt_data, decrypt_data

@pytest.mark.auth
class TestAgeGate:
    """Tests for Age Gate functionality."""

    @pytest.fixture
    def auth_with_age_gate(self, db_manager):
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
        
        manager = AuthManager(db_manager.db)
        yield manager
        
        # Restore config
        config.set("authentication", old_config)

    @pytest.fixture
    def auth_with_dob_gate(self, db_manager):
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
        
        manager = AuthManager(db_manager.db)
        yield manager
        
        # Restore config
        config.set("authentication", old_config)

    def test_boolean_mode_success(self, auth_with_age_gate):
        """Test registration success with boolean age verification."""
        username = f"user_bool_{uuid.uuid4().hex[:6]}"
        user = auth_with_age_gate.register(
            username=username,
            email=f"{username}@example.com",
            password="SecurePassword123!",
            age=15
        )
        assert user.age_verified is True
        assert user.date_of_birth is None

    def test_boolean_mode_underage(self, auth_with_age_gate):
        """Test registration failure for underage user in boolean mode."""
        username = f"user_young_{uuid.uuid4().hex[:6]}"
        with pytest.raises(AuthError) as exc:
            auth_with_age_gate.register(
                username=username,
                email=f"{username}@example.com",
                password="SecurePassword123!",
                age=10
            )
        assert "Minimum age" in str(exc.value)

    def test_boolean_mode_missing_age(self, auth_with_age_gate):
        """Test registration failure when age is missing in boolean mode."""
        username = f"user_noage_{uuid.uuid4().hex[:6]}"
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
        username = f"user_dob_{uuid.uuid4().hex[:6]}"
        # User is 23 years old (assuming current year 2026 per context)
        # 2026 - 2003 = 23 > 18
        dob = "2003-05-20"
        
        user = auth_with_dob_gate.register(
            username=username,
            email=f"{username}@example.com",
            password="SecurePassword123!",
            dob=dob
        )
        assert user.age_verified is True
        # DOB should be available on the user object (manager decrypts it)
        assert user.date_of_birth == dob
        
        # Verify it is encrypted in DB
        db = auth_with_dob_gate._db
        row = db.fetch_one("SELECT date_of_birth FROM auth_users WHERE id = ?", (user.id,))
        assert row["date_of_birth"] != dob
        assert "ENC:" in row["date_of_birth"] # Assuming standard encryption format

    def test_dob_mode_underage(self, auth_with_dob_gate):
        """Test registration failure for underage user in DOB mode."""
        username = f"user_dob_young_{uuid.uuid4().hex[:6]}"
        # User is 10 years old (2026 - 2016 = 10 < 18)
        dob = "2016-01-01"
        
        with pytest.raises(AuthError) as exc:
            auth_with_dob_gate.register(
                username=username,
                email=f"{username}@example.com",
                password="SecurePassword123!",
                dob=dob
            )
        assert "Minimum age" in str(exc.value)

    def test_dob_mode_invalid_format(self, auth_with_dob_gate):
        """Test registration failure with invalid DOB format."""
        username = f"user_bad_dob_{uuid.uuid4().hex[:6]}"
        
        with pytest.raises(AuthError) as exc:
            auth_with_dob_gate.register(
                username=username,
                email=f"{username}@example.com",
                password="SecurePassword123!",
                dob="20-05-2000" # Wrong format
            )
        assert "Invalid date format" in str(exc.value)
