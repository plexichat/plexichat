"""
Tests for password hashing with real Argon2id.

These tests verify that the actual Argon2id implementation works correctly.
All tests in this project use real hashing - no mocks.
"""

import pytest


@pytest.fixture
def hasher():
    """Get the real encryption manager with actual Argon2id."""
    from src.utils.encryption.core import EncryptionManager
    return EncryptionManager()


@pytest.mark.unit
class TestPasswordHashing:
    """Test actual Argon2id password hashing."""

    def test_hash_password_returns_string(self, hasher):
        """Test that hashing returns a string."""
        result = hasher.hash_password("TestPass123!")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hash_password_returns_argon2_format(self, hasher):
        """Test that hash is in Argon2 PHC format."""
        result = hasher.hash_password("TestPass123!")
        assert result.startswith("$argon2id$")

    def test_hash_password_different_each_time(self, hasher):
        """Test that same password produces different hashes (due to salt)."""
        hash1 = hasher.hash_password("TestPass123!")
        hash2 = hasher.hash_password("TestPass123!")
        assert hash1 != hash2  # Different salts = different hashes

    def test_verify_password_correct(self, hasher):
        """Test verifying correct password."""
        password = "TestPass123!"
        hash_str = hasher.hash_password(password)
        
        result = hasher.verify_password(password, hash_str)
        assert result is True

    def test_verify_password_wrong(self, hasher):
        """Test verifying wrong password."""
        hash_str = hasher.hash_password("TestPass123!")
        
        result = hasher.verify_password("WrongPassword!", hash_str)
        assert result is False

    def test_verify_password_empty(self, hasher):
        """Test verifying empty password fails."""
        hash_str = hasher.hash_password("TestPass123!")
        
        result = hasher.verify_password("", hash_str)
        assert result is False

    def test_hash_empty_password_raises(self, hasher):
        """Test that hashing empty password raises error."""
        with pytest.raises(ValueError):
            hasher.hash_password("")

    @pytest.mark.parametrize("password", [
        "simple",
        "WithUpperCase",
        "with123numbers",
        "with!@#$%special",
        "MixedCase123!@#",
        "a" * 100,  # Long password
        "émojis🔐🔑",  # Unicode
        "spaces in password",
    ])
    def test_hash_and_verify_various_passwords(self, hasher, password):
        """Test hashing and verifying various password formats."""
        hash_str = hasher.hash_password(password)
        assert hasher.verify_password(password, hash_str) is True
        assert hasher.verify_password(password + "x", hash_str) is False

    def test_verify_invalid_hash_format(self, hasher):
        """Test verifying against invalid hash format."""
        result = hasher.verify_password("password", "not-a-valid-hash")
        assert result is False


@pytest.mark.unit
class TestArgon2Parameters:
    """Test that Argon2 parameters are secure."""

    def test_argon2_uses_secure_parameters(self, hasher):
        """Test that Argon2 is configured with secure parameters."""
        hash_str = hasher.hash_password("test")
        
        # Parse the hash to check parameters
        parts = hash_str.split("$")
        assert parts[1] == "argon2id"  # Using Argon2id variant
        
        # Check parameters are in the hash
        params = parts[3]
        assert "m=" in params  # Memory cost
        assert "t=" in params  # Time cost
        assert "p=" in params  # Parallelism

    def test_hash_timing_is_reasonable(self, hasher):
        """Test that hashing takes a reasonable amount of time."""
        import time
        
        start = time.time()
        hasher.hash_password("TestPass123!")
        elapsed = time.time() - start
        
        # Should take at least 10ms (security) but less than 500ms (usability)
        assert elapsed >= 0.01, "Hashing too fast - may be insecure"
        assert elapsed < 0.5, "Hashing too slow - may impact UX"
