"""
Tests for licensing public API module.
"""

import json
import base64
import time
from pathlib import Path

import pytest

import utils.licensing as license


class TestSetup:
    """Tests for the setup() function."""

    def test_setup_with_no_license_goes_free_tier(self, tmp_path, monkeypatch):
        """Test that setup without license enters free tier mode."""
        # Clear any existing env var
        monkeypatch.delenv("PLEXICHAT_LICENSE", raising=False)

        # Use temp home directory to avoid existing license files
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = license.setup()

        assert result is False
        assert license.is_free_tier() is True
        assert license.is_valid() is False

    def test_setup_with_explicit_license_path(self, tmp_path):
        """Test setup with explicit license file path."""
        # Generate a valid license
        from utils.licensing.core import generate_keypair, sign_license

        private_key, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        license_data = {
            "version": "1.0",
            "instance_id": "test-instance",
            "issued_at": int(time.time()) - 86400,
            "expires_at": int(time.time()) + 86400,  # Tomorrow
            "features": {"bond": True},
            "limits": {},
        }

        license_data["signature"] = sign_license(private_key, license_data)

        # Write to file
        license_file = tmp_path / "test_license.json"
        license_file.write_text(json.dumps(license_data))

        # Setup with explicit path
        result = license.setup(
            public_key_base64=public_b64, license_path=str(license_file)
        )

        assert result is True
        assert license.is_valid() is True
        assert license.is_free_tier() is False
        assert license.get_instance_id() == "test-instance"

    def test_setup_with_env_var_file_path(self, tmp_path, monkeypatch):
        """Test setup with PLEXICHAT_LICENSE env var as file path."""
        from utils.licensing.core import generate_keypair, sign_license

        private_key, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        license_data = {
            "version": "1.0",
            "instance_id": "env-file-instance",
            "issued_at": int(time.time()) - 86400,
            "expires_at": int(time.time()) + 86400,
            "features": {},
            "limits": {},
        }

        license_data["signature"] = sign_license(private_key, license_data)

        license_file = tmp_path / "env_license.json"
        license_file.write_text(json.dumps(license_data))

        monkeypatch.setenv("PLEXICHAT_LICENSE", str(license_file))

        result = license.setup(public_key_base64=public_b64)

        assert result is True
        assert license.get_instance_id() == "env-file-instance"

    def test_setup_with_env_var_base64(self, tmp_path, monkeypatch):
        """Test setup with PLEXICHAT_LICENSE env var as base64."""
        from utils.licensing.core import generate_keypair, sign_license

        private_key, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        license_data = {
            "version": "1.0",
            "instance_id": "env-b64-instance",
            "issued_at": int(time.time()) - 86400,
            "expires_at": int(time.time()) + 86400,
            "features": {"bond": True},
            "limits": {},
        }

        license_data["signature"] = sign_license(private_key, license_data)

        # Encode as base64
        json_str = json.dumps(license_data)
        b64_license = base64.b64encode(json_str.encode()).decode()

        monkeypatch.setenv("PLEXICHAT_LICENSE", b64_license)

        result = license.setup(public_key_base64=public_b64)

        assert result is True
        assert license.get_instance_id() == "env-b64-instance"

    def test_setup_invalid_license_soft_fail_to_free_tier(self, tmp_path):
        """Test that invalid license causes soft-fail to free tier."""
        # Create invalid license (bad signature)
        license_data = {
            "version": "1.0",
            "instance_id": "invalid-instance",
            "issued_at": int(time.time()) - 86400,
            "expires_at": int(time.time()) + 86400,
            "features": {},
            "limits": {},
            "signature": "invalid-signature",
        }

        license_file = tmp_path / "invalid_license.json"
        license_file.write_text(json.dumps(license_data))

        # Use hardcoded public key - signature will fail verification
        result = license.setup(license_path=str(license_file))

        assert result is False  # Soft fail
        assert license.is_free_tier() is True
        assert license.is_valid() is False


class TestFreeTierMode:
    """Tests for free tier behavior."""

    def test_free_tier_has_feature_returns_defaults(self, tmp_path, monkeypatch):
        """Test that has_feature returns defaults in free tier."""
        monkeypatch.delenv("PLEXICHAT_LICENSE", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        license.setup()

        # Premium features should return False
        assert license.has_feature("bond") is False
        assert license.has_feature("sso") is False

        # Base features with default=True should return True
        assert license.has_feature("messaging", default=True) is True
        assert license.has_feature("servers", default=True) is True

    def test_free_tier_get_feature_config_returns_default(self, tmp_path, monkeypatch):
        """Test that get_feature_config returns default in free tier."""
        monkeypatch.delenv("PLEXICHAT_LICENSE", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        license.setup()

        assert license.get_feature_config("advanced_automod") is None
        assert license.get_feature_config("advanced_automod", default={}) == {}

    def test_free_tier_get_feature_limit_returns_default(self, tmp_path, monkeypatch):
        """Test that get_feature_limit returns default in free tier."""
        monkeypatch.delenv("PLEXICHAT_LICENSE", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        license.setup()

        assert license.get_feature_limit("max_users") is None
        assert license.get_feature_limit("max_users", default=100) == 100

    def test_free_tier_get_instance_id_returns_none(self, tmp_path, monkeypatch):
        """Test that get_instance_id returns None in free tier."""
        monkeypatch.delenv("PLEXICHAT_LICENSE", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        license.setup()

        assert license.get_instance_id() is None

    def test_free_tier_get_license_source_returns_none(self, tmp_path, monkeypatch):
        """Test that get_license_source returns None in free tier."""
        monkeypatch.delenv("PLEXICHAT_LICENSE", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        license.setup()

        assert license.get_license_source() is None


class TestLicensedMode:
    """Tests for behavior with valid license."""

    @pytest.fixture
    def valid_license_setup(self, tmp_path):
        """Create a valid license and set it up."""
        from utils.licensing.core import generate_keypair, sign_license

        private_key, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        license_data = {
            "version": "1.0",
            "instance_id": "licensed-instance",
            "issued_at": int(time.time()) - 86400,
            "expires_at": int(time.time()) + 86400 * 365,  # 1 year
            "features": {
                "bond": True,
                "sso": False,
                "advanced_automod": {"max_rules": 50, "ai_enabled": True},
            },
            "limits": {
                "max_users": 1000,
                "max_servers": 100,
            },
        }

        license_data["signature"] = sign_license(private_key, license_data)

        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps(license_data))

        # Reset module state
        license._setup_called = False
        license._free_tier_mode = False
        license._license_manager = None

        result = license.setup(
            public_key_base64=public_b64, license_path=str(license_file)
        )

        assert result is True

        yield

        # Cleanup
        license._setup_called = False
        license._free_tier_mode = False
        license._license_manager = None

    def test_licensed_is_valid_true(self, valid_license_setup):
        """Test is_valid returns True with valid license."""
        assert license.is_valid() is True

    def test_licensed_is_free_tier_false(self, valid_license_setup):
        """Test is_free_tier returns False with valid license."""
        assert license.is_free_tier() is False

    def test_licensed_has_feature_boolean(self, valid_license_setup):
        """Test has_feature with boolean features."""
        assert license.has_feature("bond") is True
        assert license.has_feature("sso") is False

    def test_licensed_has_feature_tiered(self, valid_license_setup):
        """Test has_feature with tiered features."""
        # Tiered features return True (they exist)
        assert license.has_feature("advanced_automod") is True

    def test_licensed_get_feature_config(self, valid_license_setup):
        """Test get_feature_config returns full config."""
        config = license.get_feature_config("advanced_automod")
        assert config == {"max_rules": 50, "ai_enabled": True}

    def test_licensed_get_feature_limit(self, valid_license_setup):
        """Test get_feature_limit returns correct values."""
        assert license.get_feature_limit("max_users") == 1000
        assert license.get_feature_limit("max_servers") == 100

    def test_licensed_get_instance_id(self, valid_license_setup):
        """Test get_instance_id returns correct value."""
        assert license.get_instance_id() == "licensed-instance"

    def test_licensed_get_license_source(self, valid_license_setup):
        """Test get_license_source returns file path."""
        source = license.get_license_source()
        assert source is not None
        assert "file:" in source

    def test_licensed_get_validation_result(self, valid_license_setup):
        """Test get_validation_result returns valid result."""
        result = license.get_validation_result()
        assert result is not None
        assert result.is_valid is True
        assert result.is_expired is False
        assert result.is_signature_valid is True
        assert result.instance_id == "licensed-instance"

    def test_licensed_get_expiry_timestamp(self, valid_license_setup):
        """Test get_expiry_timestamp returns future timestamp."""
        expiry = license.get_expiry_timestamp()
        assert expiry is not None
        assert expiry > int(time.time())

    def test_licensed_to_dict(self, valid_license_setup):
        """Test to_dict returns full license data."""
        data = license.to_dict()
        assert data["version"] == "1.0"
        assert data["instance_id"] == "licensed-instance"
        assert data["features"]["bond"] is True


class TestErrorHandling:
    """Tests for error conditions."""

    def test_setup_called_twice_resets_state(self, tmp_path):
        """Test that calling setup twice resets the state."""
        from utils.licensing.core import generate_keypair, sign_license

        # First setup
        private_key, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        license_data = {
            "version": "1.0",
            "instance_id": "first-instance",
            "issued_at": int(time.time()) - 86400,
            "expires_at": int(time.time()) + 86400,
            "features": {},
            "limits": {},
        }
        license_data["signature"] = sign_license(private_key, license_data)

        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps(license_data))

        result1 = license.setup(
            public_key_base64=public_b64, license_path=str(license_file)
        )
        assert result1 is True
        assert license.get_instance_id() == "first-instance"

        # Second setup - should reset
        license_data2 = license_data.copy()
        license_data2["instance_id"] = "second-instance"
        license_data2["signature"] = sign_license(private_key, license_data2)

        license_file2 = tmp_path / "license2.json"
        license_file2.write_text(json.dumps(license_data2))

        result2 = license.setup(
            public_key_base64=public_b64, license_path=str(license_file2)
        )
        assert result2 is True
        assert license.get_instance_id() == "second-instance"

    def test_functions_raise_if_setup_not_called(self):
        """Test that API functions raise if setup() not called."""
        # Reset state
        license._setup_called = False
        license._license_manager = None

        with pytest.raises(RuntimeError, match="license.setup"):
            license.is_valid()

        with pytest.raises(RuntimeError, match="license.setup"):
            license.has_feature("bond")

        with pytest.raises(RuntimeError, match="license.setup"):
            license.get_instance_id()


class TestConstants:
    """Tests for module constants."""

    def test_default_license_path(self):
        """Test that DEFAULT_LICENSE_PATH is correct."""
        expected = Path.home() / ".plexichat" / "config" / "license"
        assert license.DEFAULT_LICENSE_PATH == expected

    def test_license_env_var(self):
        """Test that LICENSE_ENV_VAR is correct."""
        assert license.LICENSE_ENV_VAR == "PLEXICHAT_LICENSE"


class TestExpiration:
    """Tests for license expiration handling."""

    def test_expired_license_goes_free_tier(self, tmp_path):
        """Test that expired license causes free tier mode."""
        from utils.licensing.core import generate_keypair, sign_license

        private_key, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        # Create expired license
        license_data = {
            "version": "1.0",
            "instance_id": "expired-instance",
            "issued_at": int(time.time()) - 86400 * 60,  # 60 days ago
            "expires_at": int(time.time()) - 86400,  # Expired yesterday
            "features": {"bond": True},
            "limits": {},
        }

        license_data["signature"] = sign_license(private_key, license_data)

        license_file = tmp_path / "expired_license.json"
        license_file.write_text(json.dumps(license_data))

        result = license.setup(
            public_key_base64=public_b64, license_path=str(license_file)
        )

        # Soft fail to free tier
        assert result is False
        assert license.is_free_tier() is True
        assert license.has_feature("bond") is False  # Expired = no features

    def test_perpetual_license_never_expires(self, tmp_path):
        """Test that perpetual licenses (no expiry) work correctly."""
        from utils.licensing.core import generate_keypair, sign_license

        private_key, public_key = generate_keypair()
        public_b64 = base64.b64encode(public_key).decode()

        license_data = {
            "version": "1.0",
            "instance_id": "perpetual-instance",
            "issued_at": int(time.time()) - 86400 * 365,  # 1 year ago
            "expires_at": None,  # Perpetual
            "features": {"bond": True},
            "limits": {},
        }

        license_data["signature"] = sign_license(private_key, license_data)

        license_file = tmp_path / "perpetual_license.json"
        license_file.write_text(json.dumps(license_data))

        result = license.setup(
            public_key_base64=public_b64, license_path=str(license_file)
        )

        assert result is True
        assert license.is_valid() is True
        assert license.get_expiry_timestamp() is None


class TestExports:
    """Tests that all expected symbols are exported."""

    def test_all_classes_exported(self):
        """Test that all classes are available in public API."""
        assert hasattr(license, "License")
        assert hasattr(license, "LicenseManager")
        assert hasattr(license, "LicenseValidationResult")

    def test_all_exceptions_exported(self):
        """Test that all exceptions are available."""
        assert hasattr(license, "LicenseError")
        assert hasattr(license, "InvalidLicenseError")
        assert hasattr(license, "SignatureVerificationError")
        assert hasattr(license, "ExpiredLicenseError")

    def test_all_functions_exported(self):
        """Test that all functions are available."""
        assert callable(license.setup)
        assert callable(license.is_valid)
        assert callable(license.is_free_tier)
        assert callable(license.has_feature)
        assert callable(license.get_feature_config)
        assert callable(license.get_feature_limit)
        assert callable(license.get_instance_id)
        assert callable(license.get_license_source)
        assert callable(license.get_validation_result)
        assert callable(license.get_expiry_timestamp)
        assert callable(license.to_dict)
        assert callable(license.generate_keypair)
        assert callable(license.sign_license)
