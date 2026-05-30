"""
Tests for licensing core module.
"""

import json
import base64
import time

import pytest

from utils.licensing.core import (
    License,
    LicenseManager,
    LicenseValidationResult,
    InvalidLicenseError,
    generate_keypair,
    sign_license,
)


class TestLicenseDataclass:
    """Tests for the License dataclass."""

    def test_valid_license_creation(self):
        """Test creating a valid license object."""
        license_obj = License(
            version="1.0",
            instance_id="test-instance",
            issued_at=1746292800,
            expires_at=1777828800,
            features={"bond": True, "sso": False},
            limits={"max_users": 1000},
            signature="test-sig",
        )

        assert license_obj.version == "1.0"
        assert license_obj.instance_id == "test-instance"
        assert license_obj.issued_at == 1746292800
        assert license_obj.expires_at == 1777828800
        assert license_obj.features == {"bond": True, "sso": False}
        assert license_obj.limits == {"max_users": 1000}
        assert license_obj.signature == "test-sig"

    def test_license_without_expiry(self):
        """Test creating a perpetual license (no expiry)."""
        license_obj = License(
            version="1.0",
            instance_id="perpetual-instance",
            issued_at=1746292800,
            expires_at=None,
            features={},
            limits={},
        )

        assert license_obj.expires_at is None

    def test_empty_version_raises_error(self):
        """Test that empty version raises InvalidLicenseError."""
        with pytest.raises(InvalidLicenseError, match="version cannot be empty"):
            License(
                version="",
                instance_id="test",
                issued_at=1746292800,
                expires_at=None,
            )

    def test_empty_instance_id_raises_error(self):
        """Test that empty instance_id raises InvalidLicenseError."""
        with pytest.raises(InvalidLicenseError, match="Instance ID cannot be empty"):
            License(
                version="1.0",
                instance_id="",
                issued_at=1746292800,
                expires_at=None,
            )

    def test_invalid_timestamp_raises_error(self):
        """Test that invalid timestamp raises InvalidLicenseError."""
        with pytest.raises(InvalidLicenseError, match="Invalid issued_at"):
            License(
                version="1.0",
                instance_id="test",
                issued_at=0,
                expires_at=None,
            )

    def test_expiry_before_issue_raises_error(self):
        """Test that expiry before issue date raises error."""
        with pytest.raises(
            InvalidLicenseError, match="Expiry must be after issue date"
        ):
            License(
                version="1.0",
                instance_id="test",
                issued_at=1746292800,
                expires_at=1746292700,  # Before issue
            )


class TestLicenseManager:
    """Tests for LicenseManager class."""

    def test_manager_initialization(self):
        """Test initializing LicenseManager."""
        manager = LicenseManager()
        assert manager._license is None
        assert manager._license_source is None

    def test_load_from_dict(self):
        """Test loading license from dictionary."""
        manager = LicenseManager()
        data = {
            "version": "1.0",
            "instance_id": "test-instance",
            "issued_at": 1746292800,
            "expires_at": 1777828800,
            "features": {"bond": True},
            "limits": {"max_users": 1000},
            "signature": "test-sig",
        }

        license_obj = manager.load_from_dict(data)

        assert license_obj.instance_id == "test-instance"
        assert manager._license_source == "dict"

    def test_load_from_file(self, tmp_path):
        """Test loading license from JSON file."""
        manager = LicenseManager()
        license_file = tmp_path / "test_license.json"

        data = {
            "version": "1.0",
            "instance_id": "file-instance",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {},
            "limits": {},
        }

        license_file.write_text(json.dumps(data))

        license_obj = manager.load_from_file(str(license_file))

        assert license_obj.instance_id == "file-instance"
        assert manager._license_source == f"file:{license_file}"

    def test_load_from_nonexistent_file_raises_error(self, tmp_path):
        """Test that loading from non-existent file raises error."""
        manager = LicenseManager()

        with pytest.raises(InvalidLicenseError, match="License file not found"):
            manager.load_from_file(str(tmp_path / "nonexistent.json"))

    def test_load_from_malformed_json_raises_error(self, tmp_path):
        """Test that loading malformed JSON raises error."""
        manager = LicenseManager()
        license_file = tmp_path / "bad_license.json"
        license_file.write_text("not valid json {{{")

        with pytest.raises(InvalidLicenseError, match="Invalid JSON"):
            manager.load_from_file(str(license_file))

    def test_load_from_base64(self):
        """Test loading license from base64-encoded JSON."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "b64-instance",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {"bond": True},
            "limits": {},
        }

        json_str = json.dumps(data)
        b64_data = base64.b64encode(json_str.encode()).decode()

        license_obj = manager.load_from_base64(b64_data)

        assert license_obj.instance_id == "b64-instance"
        assert manager._license_source == "env:b64"

    def test_load_from_invalid_base64_raises_error(self):
        """Test that loading invalid base64 raises error."""
        manager = LicenseManager()

        with pytest.raises(InvalidLicenseError, match="Cannot decode base64"):
            manager.load_from_base64("not-valid-base64!!!")


class TestLicenseValidation:
    """Tests for license validation."""

    def test_validate_no_license(self):
        """Test validation when no license is loaded."""
        manager = LicenseManager()

        result = manager.validate()

        assert not result.is_valid
        assert not result.is_expired
        assert not result.is_signature_valid
        assert result.error_message and "No license loaded" in result.error_message

    def test_validate_valid_license_no_signature_check(self):
        """Test validation of license without signature verification."""
        manager = LicenseManager()

        # Future expiry
        future_time = int(time.time()) + 86400  # Tomorrow

        data = {
            "version": "1.0",
            "instance_id": "valid-instance",
            "issued_at": int(time.time()) - 86400,  # Yesterday
            "expires_at": future_time,
            "features": {},
            "limits": {},
        }

        manager.load_from_dict(data)
        result = manager.validate()

        # Without public key, signature check will fail
        # But expiry should be correct
        assert result.is_expired is False

    def test_validate_expired_license(self):
        """Test validation detects expired license."""
        manager = LicenseManager()

        # Past expiry
        past_time = int(time.time()) - 86400  # Yesterday

        data = {
            "version": "1.0",
            "instance_id": "expired-instance",
            "issued_at": past_time - 86400 * 30,  # Month ago
            "expires_at": past_time,
            "features": {},
            "limits": {},
        }

        manager.load_from_dict(data)
        result = manager.validate()

        assert result.is_expired is True
        assert not result.is_valid  # Expired licenses are invalid
        assert result.error_message and "License has expired" in result.error_message

    def test_validate_perpetual_license_not_expired(self):
        """Test that perpetual licenses (no expiry) are not expired."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "perpetual-instance",
            "issued_at": int(time.time()) - 86400,
            "expires_at": None,  # Perpetual
            "features": {},
            "limits": {},
        }

        manager.load_from_dict(data)
        result = manager.validate()

        assert result.is_expired is False


class TestFeatureChecking:
    """Tests for feature checking methods."""

    def test_has_feature_boolean_true(self):
        """Test checking boolean feature that is enabled."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "test",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {"bond": True, "sso": True},
            "limits": {},
        }

        manager.load_from_dict(data)
        # Bypass signature validation for this test
        manager._validation_result = LicenseValidationResult(
            is_valid=True,
            is_expired=False,
            is_signature_valid=True,
            instance_id="test",
            error_message=None,
        )

        assert manager.has_feature("bond") is True
        assert manager.has_feature("sso") is True

    def test_has_feature_boolean_false(self):
        """Test checking boolean feature that is disabled."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "test",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {"bond": False},
            "limits": {},
        }

        manager.load_from_dict(data)
        manager._validation_result = LicenseValidationResult(
            is_valid=True,
            is_expired=False,
            is_signature_valid=True,
            instance_id="test",
            error_message=None,
        )

        assert manager.has_feature("bond") is False

    def test_has_feature_missing_uses_default(self):
        """Test that missing features return default value."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "test",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {},
            "limits": {},
        }

        manager.load_from_dict(data)
        manager._validation_result = LicenseValidationResult(
            is_valid=True,
            is_expired=False,
            is_signature_valid=True,
            instance_id="test",
            error_message=None,
        )

        assert manager.has_feature("bond", default=False) is False
        assert manager.has_feature("bond", default=True) is True

    def test_has_feature_tiered_returns_true(self):
        """Test that tiered features (dict values) return True."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "test",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {"advanced_automod": {"max_rules": 50, "ai_enabled": True}},
            "limits": {},
        }

        manager.load_from_dict(data)
        manager._validation_result = LicenseValidationResult(
            is_valid=True,
            is_expired=False,
            is_signature_valid=True,
            instance_id="test",
            error_message=None,
        )

        # Tiered features return True (they exist and are enabled)
        assert manager.has_feature("advanced_automod") is True

    def test_get_feature_config(self):
        """Test getting full feature configuration."""
        manager = LicenseManager()

        automod_config = {"max_rules": 50, "ai_enabled": True}

        data = {
            "version": "1.0",
            "instance_id": "test",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {
                "advanced_automod": automod_config,
                "bond": True,
            },
            "limits": {},
        }

        manager.load_from_dict(data)
        manager._validation_result = LicenseValidationResult(
            is_valid=True,
            is_expired=False,
            is_signature_valid=True,
            instance_id="test",
            error_message=None,
        )

        # Tiered feature returns dict
        assert manager.get_feature_config("advanced_automod") == automod_config

        # Boolean feature returns its value
        assert manager.get_feature_config("bond") is True

    def test_get_feature_limit(self):
        """Test getting resource limits."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "test",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {},
            "limits": {
                "max_users": 1000,
                "max_servers": 100,
            },
        }

        manager.load_from_dict(data)
        manager._validation_result = LicenseValidationResult(
            is_valid=True,
            is_expired=False,
            is_signature_valid=True,
            instance_id="test",
            error_message=None,
        )

        assert manager.get_feature_limit("max_users") == 1000
        assert manager.get_feature_limit("max_servers") == 100
        assert manager.get_feature_limit("nonexistent", default=50) == 50

    def test_get_instance_id(self):
        """Test getting instance ID from valid license."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "my-instance-001",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {},
            "limits": {},
        }

        manager.load_from_dict(data)
        manager._validation_result = LicenseValidationResult(
            is_valid=True,
            is_expired=False,
            is_signature_valid=True,
            instance_id="my-instance-001",
            error_message=None,
        )

        assert manager.get_instance_id() == "my-instance-001"

    def test_get_instance_id_invalid_license(self):
        """Test that invalid license returns None for instance ID."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "invalid-instance",
            "issued_at": 1746292800,
            "expires_at": None,
            "features": {},
            "limits": {},
        }

        manager.load_from_dict(data)
        manager._validation_result = LicenseValidationResult(
            is_valid=False,
            is_expired=False,
            is_signature_valid=False,
            instance_id=None,
            error_message="Invalid signature",
        )

        assert manager.get_instance_id() is None


class TestCryptography:
    """Tests for cryptographic functions."""

    def test_generate_keypair(self):
        """Test generating Ed25519 keypair."""
        private_key, public_key = generate_keypair()

        assert len(private_key) == 32
        assert len(public_key) == 32
        assert private_key != public_key

    def test_sign_and_verify_license(self):
        """Test signing a license and verifying the signature."""
        # Generate a keypair
        private_key, public_key = generate_keypair()

        # Create license data
        license_data = {
            "version": "1.0",
            "instance_id": "signed-instance",
            "issued_at": int(time.time()),
            "expires_at": None,
            "features": {"bond": True},
            "limits": {"max_users": 1000},
        }

        # Sign the license
        signature = sign_license(private_key, license_data)

        # Verify signature format
        assert isinstance(signature, str)
        sig_bytes = base64.b64decode(signature)
        assert len(sig_bytes) == 64  # Ed25519 signature size

        # Add signature to license
        license_data["signature"] = signature

        # Verify with LicenseManager
        manager = LicenseManager(public_key_bytes=public_key)
        manager.load_from_dict(license_data)
        result = manager.validate()

        assert result.is_signature_valid is True
        assert result.is_valid is True

    def test_signature_verification_fails_with_wrong_key(self):
        """Test that signature verification fails with wrong public key."""
        # Generate two different keypairs
        private_key1, public_key1 = generate_keypair()
        _, public_key2 = generate_keypair()

        # Create and sign license with keypair 1
        license_data = {
            "version": "1.0",
            "instance_id": "test",
            "issued_at": int(time.time()),
            "expires_at": None,
            "features": {},
            "limits": {},
        }

        signature = sign_license(private_key1, license_data)
        license_data["signature"] = signature

        # Try to verify with keypair 2's public key
        manager = LicenseManager(public_key_bytes=public_key2)
        manager.load_from_dict(license_data)
        result = manager.validate()

        assert result.is_signature_valid is False
        assert not result.is_valid

    def test_tampered_license_fails_verification(self):
        """Test that modified license fails signature verification."""
        # Generate keypair
        private_key, public_key = generate_keypair()

        # Create and sign license
        license_data = {
            "version": "1.0",
            "instance_id": "original-instance",
            "issued_at": int(time.time()),
            "expires_at": None,
            "features": {},
            "limits": {},
        }

        signature = sign_license(private_key, license_data)
        license_data["signature"] = signature

        # Tamper with the data
        license_data["instance_id"] = "tampered-instance"

        # Verification should fail
        manager = LicenseManager(public_key_bytes=public_key)
        manager.load_from_dict(license_data)
        result = manager.validate()

        assert result.is_signature_valid is False
        assert not result.is_valid


class TestLicenseConversion:
    """Tests for license serialization."""

    def test_to_dict(self):
        """Test converting license to dictionary."""
        manager = LicenseManager()

        data = {
            "version": "1.0",
            "instance_id": "test-instance",
            "issued_at": 1746292800,
            "expires_at": 1777828800,
            "features": {"bond": True},
            "limits": {"max_users": 1000},
            "signature": "test-signature",
        }

        manager.load_from_dict(data)
        result_dict = manager.to_dict()

        assert result_dict["version"] == "1.0"
        assert result_dict["instance_id"] == "test-instance"
        assert result_dict["issued_at"] == 1746292800
        assert result_dict["features"] == {"bond": True}
        assert result_dict["signature"] == "test-signature"

    def test_to_dict_no_license(self):
        """Test to_dict returns empty dict when no license loaded."""
        manager = LicenseManager()

        assert manager.to_dict() == {}


class TestPublicKeyConfiguration:
    """Tests for public key configuration."""

    def test_set_public_key_class_method(self):
        """Test setting public key via class method."""
        _, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        LicenseManager.set_public_key(public_b64)

        # Create new manager should use the set key
        manager = LicenseManager()
        assert manager._PUBLIC_KEY_BASE64 == public_b64

    def test_constructor_public_key_override(self):
        """Test that constructor public key overrides class key."""
        # Set a key via class method
        _, public_key1 = generate_keypair()
        public_b64_1 = base64.b64encode(public_key1).decode()
        LicenseManager.set_public_key(public_b64_1)

        # But pass different key to constructor
        _, public_key2 = generate_keypair()

        manager = LicenseManager(public_key_bytes=public_key2)

        # Constructor key should be used
        assert manager._public_key == public_key2
