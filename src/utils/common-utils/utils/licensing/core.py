"""
Licensing core module - Asymmetric cryptography-based license validation for Plexichat.

Uses Ed25519 for cryptographic signatures. Licenses are JSON-based with a base64-encoded
signature that can be verified using the embedded public key.

License File Format:
{
    "version": "1.0",
    "instance_id": "plexichat-prod-001",
    "issued_at": 1746292800,
    "expires_at": 1777828800,
    "features": {
        "bond": true,
        "advanced_automod": {"max_rules": 50},
        "sso": false
    },
    "limits": {
        "max_users": 1000,
        "max_servers": 100
    },
    "signature": "base64_encoded_ed25519_signature"
}
"""

import json
import base64
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class LicenseError(Exception):
    """Base exception for licensing errors."""

    pass


class InvalidLicenseError(LicenseError):
    """Raised when a license is malformed or has invalid structure."""

    pass


class SignatureVerificationError(LicenseError):
    """Raised when license signature verification fails."""

    pass


class ExpiredLicenseError(LicenseError):
    """Raised when a license has expired."""

    pass


class FeatureNotFoundError(LicenseError):
    """Raised when a requested feature is not found in the license."""

    pass


@dataclass
class License:
    """
    Represents a Plexichat license.

    Attributes:
        version: License format version (e.g., "1.0")
        instance_id: Unique identifier for this instance
        issued_at: Unix timestamp when license was issued
        expires_at: Unix timestamp when license expires (None = never)
        features: Dict of feature names to values (bool or dict for tiered)
        limits: Dict of resource limits
        signature: Base64-encoded Ed25519 signature
    """

    version: str
    instance_id: str
    issued_at: int
    expires_at: Optional[int]
    features: Dict[str, Any] = field(default_factory=dict)
    limits: Dict[str, Any] = field(default_factory=dict)
    signature: Optional[str] = None

    def __post_init__(self):
        """Validate license structure after creation."""
        if not self.version:
            raise InvalidLicenseError("License version cannot be empty")
        if not self.instance_id:
            raise InvalidLicenseError("Instance ID cannot be empty")
        if self.issued_at <= 0:
            raise InvalidLicenseError("Invalid issued_at timestamp")
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise InvalidLicenseError("Expiry must be after issue date")


@dataclass
class LicenseValidationResult:
    """Result of license validation."""

    is_valid: bool
    is_expired: bool
    is_signature_valid: bool
    instance_id: Optional[str] = None
    error_message: Optional[str] = None


class LicenseManager:
    """
    Manages license loading, validation, and feature checking.

    Uses Ed25519 for cryptographic verification. The public key is embedded
    in the application; private key is used only for signing licenses.
    """

    # Current license format version
    CURRENT_VERSION = "1.0"

    # Ed25519 public key for Plexichat (hardcoded, base64 encoded)
    # This is the ONLY public key that should verify licenses
    _PUBLIC_KEY_BASE64 = "gBS50oixMLyikqv4Q/W3ME0cK0p5p1p2XS8KV8OKfTk="

    def __init__(self, public_key_bytes: Optional[bytes] = None):
        """
        Initialize the LicenseManager.

        Args:
            public_key_bytes: Ed25519 public key bytes. If None, uses hardcoded key.
        """
        self._public_key = public_key_bytes
        self._license: Optional[License] = None
        self._license_source: Optional[str] = None
        self._validation_result: Optional[LicenseValidationResult] = None

    @classmethod
    def set_public_key(cls, public_key_base64: str) -> None:
        """
        Set the hardcoded public key for verification.

        This should be called at module initialization with the
        official Plexichat public key.

        Args:
            public_key_base64: Base64-encoded Ed25519 public key (32 bytes)
        """
        cls._PUBLIC_KEY_BASE64 = public_key_base64

    def _get_public_key(self) -> bytes:
        """Get the public key bytes for verification."""
        if self._public_key is not None:
            return self._public_key
        if self._PUBLIC_KEY_BASE64:
            return base64.b64decode(self._PUBLIC_KEY_BASE64)
        raise LicenseError("No public key configured for license verification")

    def load_from_file(self, filepath: str) -> License:
        """
        Load a license from a JSON file.

        Args:
            filepath: Path to the license JSON file

        Returns:
            Loaded License object

        Raises:
            InvalidLicenseError: If file cannot be read or parsed
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise InvalidLicenseError(f"License file not found: {filepath}")
        except json.JSONDecodeError as e:
            raise InvalidLicenseError(f"Invalid JSON in license file: {e}")
        except Exception as e:
            raise InvalidLicenseError(f"Cannot read license file: {e}")

        self._license_source = f"file:{filepath}"
        return self._parse_license_data(data)

    def load_from_base64(self, base64_data: str) -> License:
        """
        Load a license from a base64-encoded JSON string.

        This is useful for passing licenses via environment variables.

        Args:
            base64_data: Base64-encoded license JSON

        Returns:
            Loaded License object

        Raises:
            InvalidLicenseError: If data cannot be decoded or parsed
        """
        try:
            json_bytes = base64.b64decode(base64_data)
            data = json.loads(json_bytes.decode("utf-8"))
        except Exception as e:
            raise InvalidLicenseError(f"Cannot decode base64 license data: {e}")

        self._license_source = "env:b64"
        return self._parse_license_data(data)

    def load_from_dict(self, data: Dict[str, Any]) -> License:
        """
        Load a license from a dictionary.

        Args:
            data: Dictionary containing license data

        Returns:
            Loaded License object
        """
        self._license_source = "dict"
        return self._parse_license_data(data)

    def _parse_license_data(self, data: Dict[str, Any]) -> License:
        """Parse license data dictionary into License object."""
        try:
            license_obj = License(
                version=data.get("version", "1.0"),
                instance_id=data.get("instance_id", ""),
                issued_at=data.get("issued_at", 0),
                expires_at=data.get("expires_at"),
                features=data.get("features", {}),
                limits=data.get("limits", {}),
                signature=data.get("signature"),
            )
            self._license = license_obj
            return license_obj
        except (TypeError, KeyError) as e:
            raise InvalidLicenseError(f"Invalid license structure: {e}")

    def validate(self) -> LicenseValidationResult:
        """
        Validate the loaded license.

        Checks:
        1. License structure is valid
        2. Signature is valid (if public key available)
        3. License has not expired

        Returns:
            LicenseValidationResult with detailed status
        """
        if self._license is None:
            return LicenseValidationResult(
                is_valid=False,
                is_expired=False,
                is_signature_valid=False,
                error_message="No license loaded",
            )

        now = int(time.time())

        # Check expiration
        is_expired = False
        if self._license.expires_at is not None and now > self._license.expires_at:
            is_expired = True

        # Verify signature if we have a public key
        is_signature_valid = False
        signature_error = None

        try:
            is_signature_valid = self._verify_signature()
        except Exception as e:
            signature_error = str(e)

        # Determine overall validity
        is_valid = is_signature_valid and not is_expired

        error_message = None
        if is_expired:
            error_message = "License has expired"
        elif not is_signature_valid:
            error_message = signature_error or "Invalid signature"

        self._validation_result = LicenseValidationResult(
            is_valid=is_valid,
            is_expired=is_expired,
            is_signature_valid=is_signature_valid,
            instance_id=self._license.instance_id if is_valid else None,
            error_message=error_message,
        )

        return self._validation_result

    def _verify_signature(self) -> bool:
        """
        Verify the license signature using Ed25519.

        Returns:
            True if signature is valid

        Raises:
            SignatureVerificationError: If verification fails
        """
        if not self._license or not self._license.signature:
            raise SignatureVerificationError("No signature present in license")

        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
            from cryptography.exceptions import InvalidSignature

            public_key_bytes = self._get_public_key()
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)

            # Create canonical data for verification (excluding signature)
            verify_data = {
                "version": self._license.version,
                "instance_id": self._license.instance_id,
                "issued_at": self._license.issued_at,
                "expires_at": self._license.expires_at,
                "features": self._license.features,
                "limits": self._license.limits,
            }

            # Canonical JSON representation for signing
            message = json.dumps(
                verify_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")

            signature_bytes = base64.b64decode(self._license.signature)

            try:
                public_key.verify(signature_bytes, message)
                return True
            except InvalidSignature:
                raise SignatureVerificationError("Signature verification failed")

        except ImportError:
            raise SignatureVerificationError(
                "cryptography library required for license verification"
            )
        except Exception as e:
            raise SignatureVerificationError(f"Signature verification error: {e}")

    def has_feature(self, feature_name: str, default: bool = False) -> bool:
        """
        Check if the license includes a specific feature.

        For boolean features, returns the boolean value.
        For tiered features (dict), returns True if feature exists.

        Args:
            feature_name: Name of the feature to check
            default: Default value if feature not found or license invalid

        Returns:
            True if feature is enabled, False otherwise
        """
        if self._license is None:
            return default

        if self._validation_result and not self._validation_result.is_valid:
            return default

        feature_value = self._license.features.get(feature_name)

        if feature_value is None:
            return default

        # If it's a boolean, return directly
        if isinstance(feature_value, bool):
            return feature_value

        # If it's a dict (tiered feature), it exists and is enabled
        if isinstance(feature_value, dict):
            return True

        return default

    def get_feature_config(self, feature_name: str, default: Any = None) -> Any:
        """
        Get the full configuration for a tiered feature.

        Args:
            feature_name: Name of the feature
            default: Default value if feature not found

        Returns:
            Feature configuration (dict for tiered, bool for boolean)

        Example:
            config = manager.get_feature_config("advanced_automod")
            # Returns: {"max_rules": 50, "ai_enabled": true}
        """
        if self._license is None:
            return default

        if self._validation_result and not self._validation_result.is_valid:
            return default

        return self._license.features.get(feature_name, default)

    def get_feature_limit(self, limit_name: str, default: Any = None) -> Any:
        """
        Get a specific limit from the license.

        Args:
            limit_name: Name of the limit (e.g., "max_users")
            default: Default value if limit not found

        Returns:
            Limit value
        """
        if self._license is None:
            return default

        if self._validation_result and not self._validation_result.is_valid:
            return default

        return self._license.limits.get(limit_name, default)

    def get_instance_id(self) -> Optional[str]:
        """
        Get the instance ID from the license.

        Returns:
            Instance ID if license is valid, None otherwise
        """
        if self._validation_result and self._validation_result.is_valid:
            return self._license.instance_id if self._license else None
        return None

    def get_license_source(self) -> Optional[str]:
        """
        Get the source of the loaded license.

        Returns:
            Source identifier (e.g., "file:/path", "env:b64", "dict")
        """
        return self._license_source

    def is_valid(self) -> bool:
        """
        Check if the loaded license is valid.

        Returns:
            True if license is valid and not expired
        """
        if self._validation_result is None:
            self.validate()
        return self._validation_result.is_valid if self._validation_result else False

    def get_expiry_timestamp(self) -> Optional[int]:
        """
        Get the license expiry timestamp.

        Returns:
            Unix timestamp or None if no expiry
        """
        if self._license:
            return self._license.expires_at
        return None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the license to a dictionary representation.

        Returns:
            Dictionary with all license fields
        """
        if self._license is None:
            return {}

        return {
            "version": self._license.version,
            "instance_id": self._license.instance_id,
            "issued_at": self._license.issued_at,
            "expires_at": self._license.expires_at,
            "features": self._license.features,
            "limits": self._license.limits,
            "signature": self._license.signature,
        }


def generate_keypair() -> tuple[bytes, bytes]:
    """
    Generate a new Ed25519 keypair for license signing.

    This should only be used by the license signing authority.

    Returns:
        Tuple of (private_key_bytes, public_key_bytes)
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        return (private_key.private_bytes_raw(), public_key.public_bytes_raw())
    except ImportError:
        raise LicenseError("cryptography library required for key generation")


def sign_license(private_key_bytes: bytes, license_data: Dict[str, Any]) -> str:
    """
    Sign license data with an Ed25519 private key.

    Args:
        private_key_bytes: 32-byte private key
        license_data: License data dictionary (without signature)

    Returns:
        Base64-encoded signature
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)

        # Create canonical message
        message = json.dumps(
            license_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

        signature = private_key.sign(message)
        return base64.b64encode(signature).decode("ascii")

    except ImportError:
        raise LicenseError("cryptography library required for signing")
