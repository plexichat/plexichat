"""
Licensing utility module - Asymmetric cryptography-based license validation for Plexichat.

This module provides:
- License loading from file, environment variable (base64), or default (free tier)
- Ed25519 signature verification
- Feature gating with boolean and tiered features
- Instance identification embedded in license
- Soft-fail mode with non-commercial warnings

License File Location (in priority order):
1. PLEXICHAT_LICENSE env var as file path (if file exists)
2. PLEXICHAT_LICENSE env var as base64-encoded license
3. ~/.plexichat/config/license (default location)
4. Free tier mode (no license file needed)

Environment Variable Usage:
    # As file path
    export PLEXICHAT_LICENSE=/path/to/license.json

    # As base64-encoded license (for containerized deployments)
    export PLEXICHAT_LICENSE=eyJ2ZXJzaW9uIjoiMS4wIiwiaW5zdGFuY2VfaWQiOiJwcm9kLTAxIiwuLi59

Basic Usage:
    # In main.py (setup once)
    import utils.licensing as license
    license.setup()

    # Check if license is valid
    if license.is_valid():
        print(f"Licensed instance: {license.get_instance_id()}")
    else:
        print("Running in free tier mode (non-commercial use only)")

    # Check features
    if license.has_feature("bond"):
        enable_bond_feature()

    # Get tiered feature configuration
    automod_config = license.get_feature_config("advanced_automod")
    max_rules = automod_config.get("max_rules", 10)

    # Get resource limits
    max_users = license.get_feature_limit("max_users", default=100)

Free Tier Behavior:
    When no valid license is found, the system operates in "free tier" mode:
    - All premium features are disabled (has_feature returns False)
    - Non-commercial use only (warning logged on startup)
    - All existing base features remain functional
    - License validation shows as invalid but system continues running

Instance Identification:
    Each license contains an instance_id that uniquely identifies the deployment.
    This ID is cryptographically signed and verified, preventing spoofing.

    Example instance_id formats:
    - "plexichat-prod-001" (production)
    - "acme-corp-primary" (organization-specific)
    - "dev-localhost" (development)

Commercial vs Non-Commercial:
    Plexichat is source-available software. Without a valid commercial license:
    - Large warning logged on startup: "NON-COMMERCIAL USE ONLY"
    - Premium features (has_feature checks) return False
    - All base functionality works normally

    With a valid commercial license:
    - Features listed in license are enabled
    - Instance is properly identified
    - Commercial use permitted per license terms

Feature Types:
    Boolean features: Simple on/off flags
        "bond": true
        "sso": false

    Tiered features: Configuration objects with limits
        "advanced_automod": {
            "max_rules": 50,
            "ai_enabled": true
        }

Cryptography:
    Uses Ed25519 elliptic curve signatures for license verification.
    - Public key is hardcoded in the application
    - Private key is held by Plexichat licensing authority only
    - Signatures are 64 bytes, base64-encoded in license files
    - All license data is signed except the signature field itself
"""

import os
from pathlib import Path
from typing import Any, Optional, Dict

import utils.logger as logger

from .core import (
    License,
    LicenseManager,
    LicenseValidationResult,
    LicenseError,
    InvalidLicenseError,
    SignatureVerificationError,
    ExpiredLicenseError,
    FeatureNotFoundError,
    generate_keypair,
    sign_license,
)

# Global license manager instance
_license_manager: Optional[LicenseManager] = None
_setup_called = False
_free_tier_mode = False
_public_key_configured = False

# Default license file location
DEFAULT_LICENSE_PATH = Path.home() / ".plexichat" / "config" / "license"

# Environment variable name
LICENSE_ENV_VAR = "PLEXICHAT_LICENSE"

# Hardcoded Ed25519 public key for Plexichat (base64 encoded, 32 bytes)
# This is the official Plexichat licensing public key - DO NOT MODIFY
_PLEXICHAT_PUBLIC_KEY_BASE64 = (
    "gBS50oixMLyikqv4Q/W3ME0cK0p5p1p2XS8KV8OKfTk="  # pragma: allowlist secret
)


def setup(
    public_key_base64: Optional[str] = None, license_path: Optional[str] = None
) -> bool:
    """
    Setup the licensing module. Should be called once in your main application file.

    This function attempts to load a license from various sources (in order):
    1. Explicit license_path argument
    2. PLEXICHAT_LICENSE env var as file path
    3. PLEXICHAT_LICENSE env var as base64-encoded license
    4. Default location (~/.plexichat/config/license)
    5. Free tier mode (no license)

    Args:
        public_key_base64: Base64-encoded Ed25519 public key for verification.
                          If None, uses hardcoded Plexichat public key.
        license_path: Explicit path to license file (overrides env var and default)

    Returns:
        True if a valid license was loaded, False if running in free tier mode

    Usage:
        import utils.licensing as license

        # Standard setup - auto-detects license
        is_licensed = license.setup()

        # With explicit public key (for testing)
        is_licensed = license.setup(public_key_base64="your_key_here")

        # With explicit license path
        is_licensed = license.setup(license_path="/etc/plexichat/license.json")
    """
    global _license_manager, _setup_called, _free_tier_mode, _public_key_configured
    global _PLEXICHAT_PUBLIC_KEY_BASE64

    _setup_called = True
    _free_tier_mode = False

    # Set public key
    if public_key_base64:
        _PLEXICHAT_PUBLIC_KEY_BASE64 = public_key_base64

    LicenseManager.set_public_key(_PLEXICHAT_PUBLIC_KEY_BASE64)
    _public_key_configured = bool(_PLEXICHAT_PUBLIC_KEY_BASE64)

    # Initialize license manager
    _license_manager = LicenseManager()

    # Try to load license from various sources
    loaded = _try_load_license(license_path)

    if not loaded:
        # Enter free tier mode
        _free_tier_mode = True
        logger.warning("=" * 60)
        logger.warning("NO VALID LICENSE FOUND - NON-COMMERCIAL USE ONLY")
        logger.warning("=" * 60)
        logger.warning("Plexichat is operating in free tier mode.")
        logger.warning("All premium features are disabled.")
        logger.warning("")
        logger.warning(
            "To obtain a license, contact sales@plexichat.com or visit https://plexichat.com"
        )
        logger.warning("")
        logger.warning("License search locations (in priority order):")
        logger.warning(f"  - Env var {LICENSE_ENV_VAR} (file path or base64)")
        logger.warning(f"  - {DEFAULT_LICENSE_PATH}")
        logger.warning(f"  - {DEFAULT_LICENSE_PATH}.json")
        logger.warning("=" * 60)
        return False

    # Validate the loaded license
    validation = _license_manager.validate()

    if validation.is_valid:
        logger.info(f"License validated for instance: {validation.instance_id}")
        logger.info(f"License source: {_license_manager.get_license_source()}")
        if _license_manager.get_expiry_timestamp():
            from datetime import datetime

            ts = _license_manager.get_expiry_timestamp()
            if ts is not None:
                expiry = datetime.fromtimestamp(float(ts))
                logger.info(f"License expires: {expiry.isoformat()}")
        return True
    else:
        # License exists but is invalid - soft fail to free tier
        _free_tier_mode = True
        logger.warning("=" * 60)
        logger.warning("INVALID LICENSE - NON-COMMERCIAL USE ONLY")
        logger.warning("=" * 60)
        logger.warning(f"License validation failed: {validation.error_message}")
        logger.warning("Falling back to free tier mode.")
        logger.warning("All premium features are disabled.")
        logger.warning("")
        logger.warning(
            "To obtain a license, contact sales@plexichat.com or visit https://plexichat.com"
        )
        logger.warning("=" * 60)
        return False


def _try_load_license(explicit_path: Optional[str] = None) -> bool:
    """
    Try to load license from various sources.

    Each source is tried in priority order; the first one that successfully
    loads wins, and subsequent sources are not consulted. This avoids
    unnecessary file I/O on the common case (license present at the first
    applicable source).

    Returns:
        True if license was loaded (not necessarily valid)
    """
    env_value = os.environ.get(LICENSE_ENV_VAR, "").strip()

    lm = _license_manager
    if lm is None:
        return False

    if _load_license_from_explicit_path(lm, explicit_path):
        return True
    if _load_license_from_env_path(lm, env_value):
        return True
    if _load_license_from_env_base64(lm, env_value):
        return True
    if _load_license_from_default_location(lm):
        return True

    # No license found - will enter free tier mode
    logger.debug("No license file found, entering free tier mode")
    return False


def _load_license_from_explicit_path(lm: "LicenseManager", path: Optional[str]) -> bool:
    """Attempt to load a license from an explicit ``license_path`` argument."""
    if not path:
        return False
    try:
        lm.load_from_file(path)
        logger.debug(f"Loaded license from explicit path: {path}")
        return True
    except InvalidLicenseError:
        return False


def _load_license_from_env_path(lm: "LicenseManager", env_value: str) -> bool:
    """Attempt to load a license from ``PLEXICHAT_LICENSE`` treated as a path."""
    if not env_value or env_value.startswith("eyJ"):
        return False
    if not ("/" in env_value or "\\" in env_value or "." in env_value):
        return False
    try:
        if not Path(env_value).exists():
            return False
        lm.load_from_file(env_value)
        logger.debug(f"Loaded license from env var path: {env_value}")
        return True
    except (InvalidLicenseError, OSError):
        return False


def _load_license_from_env_base64(lm: "LicenseManager", env_value: str) -> bool:
    """Attempt to load a license from ``PLEXICHAT_LICENSE`` treated as base64."""
    if not env_value or not env_value.startswith("eyJ"):
        return False
    try:
        lm.load_from_base64(env_value)
        logger.debug("Loaded license from env var (base64)")
        return True
    except InvalidLicenseError:
        return False


def _load_license_from_default_location(lm: "LicenseManager") -> bool:
    """Attempt to load a license from the default on-disk locations."""
    for _candidate in (DEFAULT_LICENSE_PATH, DEFAULT_LICENSE_PATH.with_suffix(".json")):
        try:
            if not _candidate.exists():
                continue
            lm.load_from_file(str(_candidate))
            logger.debug(f"Loaded license from default location: {_candidate}")
            return True
        except InvalidLicenseError:
            continue
    return False


def _ensure_setup() -> None:
    """Internal: Ensures setup was called before using license functions."""
    if not _setup_called:
        raise RuntimeError(
            "Licensing not configured. Please call license.setup() in your main.py file first."
        )


def _get_manager() -> LicenseManager:
    """Internal: Get the license manager instance."""
    _ensure_setup()
    if _license_manager is None:
        raise RuntimeError("License manager not initialized")
    return _license_manager


def is_valid() -> bool:
    """
    Check if a valid license is loaded.

    Returns:
        True if license is valid and not expired, False otherwise
    """
    if not _setup_called or _free_tier_mode:
        return False
    return _get_manager().is_valid()


def is_free_tier() -> bool:
    """
    Check if running in free tier mode (no valid license).

    Returns:
        True if no valid license found, False if licensed
    """
    return _free_tier_mode if _setup_called else True


def has_feature(feature_name: str, default: bool = False) -> bool:
    """
    Check if the current license includes a specific feature.

    In free tier mode, this always returns False for premium features.
    Base features should use default=True to remain available in free tier.

    Args:
        feature_name: Name of the feature to check (e.g., "bond", "sso")
        default: Default value if feature not found or in free tier

    Returns:
        True if feature is enabled in license, False otherwise

    Examples:
        # Premium feature - disabled in free tier
        if license.has_feature("bond"):
            enable_bond()

        # Base feature - always enabled (default=True)
        if license.has_feature("messaging", default=True):
            handle_message()
    """
    if not _setup_called or _free_tier_mode:
        return default
    return _get_manager().has_feature(feature_name, default)


def get_feature_config(feature_name: str, default: Any = None) -> Any:
    """
    Get the full configuration for a tiered feature.

    Tiered features are complex objects with multiple settings.
    Boolean features return their True/False value.

    Args:
        feature_name: Name of the feature
        default: Default value if feature not found or in free tier

    Returns:
        Feature configuration dict, boolean, or default

    Examples:
        # Tiered feature with configuration
        config = license.get_feature_config("advanced_automod")
        max_rules = config.get("max_rules", 10)
        ai_enabled = config.get("ai_enabled", False)

        # Boolean feature
        is_enabled = license.get_feature_config("bond", default=False)
    """
    if not _setup_called or _free_tier_mode:
        return default
    return _get_manager().get_feature_config(feature_name, default)


def get_feature_limit(limit_name: str, default: Any = None) -> Any:
    """
    Get a specific resource limit from the license.

    Args:
        limit_name: Name of the limit (e.g., "max_users", "max_servers")
        default: Default value if limit not found or in free tier

    Returns:
        Limit value or default

    Examples:
        max_users = license.get_feature_limit("max_users", default=100)
        max_servers = license.get_feature_limit("max_servers", default=10)
    """
    if not _setup_called or _free_tier_mode:
        return default
    return _get_manager().get_feature_limit(limit_name, default)


def get_instance_id() -> Optional[str]:
    """
    Get the instance ID from the license.

    Returns:
        Instance ID if license is valid, None otherwise

    Examples:
        instance_id = license.get_instance_id()
        if instance_id:
            logger.info(f"Running as instance: {instance_id}")
    """
    if not _setup_called or _free_tier_mode:
        return None
    return _get_manager().get_instance_id()


def get_license_source() -> Optional[str]:
    """
    Get the source of the loaded license.

    Returns:
        Source identifier:
        - "file:/path/to/license" - loaded from file
        - "env:b64" - loaded from base64 env var
        - "dict" - loaded from dictionary
        - None - free tier mode

    Examples:
        source = license.get_license_source()
        if source and source.startswith("file:"):
            print(f"License from: {source[5:]}")
    """
    if not _setup_called or _free_tier_mode:
        return None
    return _get_manager().get_license_source()


def get_validation_result() -> Optional[LicenseValidationResult]:
    """
    Get detailed validation results for the current license.

    Returns:
        LicenseValidationResult with is_valid, is_expired, is_signature_valid,
        instance_id, and error_message fields.

    Returns None if no license was loaded (free tier mode).

    Examples:
        result = license.get_validation_result()
        if result:
            print(f"Valid: {result.is_valid}")
            print(f"Expired: {result.is_expired}")
            print(f"Error: {result.error_message}")
    """
    if not _setup_called or _free_tier_mode or _license_manager is None:
        return None
    return _license_manager._validation_result


def get_expiry_timestamp() -> Optional[int]:
    """
    Get the license expiry timestamp.

    Returns:
        Unix timestamp when license expires, or None if no expiry/perpetual

    Examples:
        expiry = license.get_expiry_timestamp()
        if expiry:
            days_left = (expiry - time.time()) / 86400
            print(f"License expires in {days_left:.0f} days")
    """
    if not _setup_called or _free_tier_mode:
        return None
    return _get_manager().get_expiry_timestamp()


def to_dict() -> Dict[str, Any]:
    """
    Convert the current license to a dictionary.

    Returns:
        Dictionary with all license fields, or empty dict in free tier

    Examples:
        license_data = license.to_dict()
        print(json.dumps(license_data, indent=2))
    """
    if not _setup_called or _free_tier_mode:
        return {}
    return _get_manager().to_dict()


def apply_license_from_base64(license_payload: str) -> Dict[str, Any]:
    """
    Apply a new license from a base64-encoded payload string.

    This is used by the admin licensing routes to hot-swap the license.

    IMPORTANT: This function applies the license in-memory only — it does
    NOT write to the license file on disk. The change is transient and will
    be lost on restart. To make a permanent change, update the license file
    directly and call reload_license().

    Args:
        license_payload: Base64-encoded license JSON

    Returns:
        Dict with "success" (bool) and optional "error" (str)
    """
    global _free_tier_mode, _license_manager, _setup_called, _public_key_configured

    if _license_manager is None:
        _license_manager = LicenseManager()
        LicenseManager.set_public_key(_PLEXICHAT_PUBLIC_KEY_BASE64)
        _setup_called = True
        _public_key_configured = True

    try:
        _license_manager.load_from_base64(license_payload)
        validation = _license_manager.validate()

        if validation.is_valid:
            _free_tier_mode = False
            logger.info(
                f"License applied and validated for instance: {validation.instance_id}"
            )
            return {"success": True}
        else:
            _free_tier_mode = True
            logger.warning(f"License validation failed: {validation.error_message}")
            return {"success": False, "error": validation.error_message}
    except Exception as e:
        _free_tier_mode = True
        logger.error(f"License apply failed: {e}")
        return {"success": False, "error": str(e)}


def reload_license() -> bool:
    """
    Reload the license from the original source (env var or file).

    Returns:
        True if a valid license was reloaded
    """
    global _free_tier_mode
    loaded = _try_load_license()

    if loaded and _license_manager:
        validation = _license_manager.validate()
        _free_tier_mode = not validation.is_valid
        logger.info(f"License reloaded: valid={validation.is_valid}")
        return validation.is_valid
    else:
        _free_tier_mode = True
        return False


def validate_license_payload(decoded_bytes: bytes) -> Dict[str, Any]:
    """
    Validate a decoded license payload without applying it.

    Args:
        decoded_bytes: Decoded license JSON bytes

    Returns:
        Dict with "valid" (bool) and optional "error" (str), "instance_id" (str)
    """
    try:
        import json

        data = json.loads(decoded_bytes.decode("utf-8"))

        temp_mgr = LicenseManager()
        temp_mgr.set_public_key(_PLEXICHAT_PUBLIC_KEY_BASE64)
        temp_mgr.load_from_dict(data)
        result = temp_mgr.validate()

        return {
            "valid": result.is_valid,
            "error": result.error_message,
            "instance_id": result.instance_id,
            "is_expired": result.is_expired,
            "is_signature_valid": result.is_signature_valid,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


# Expose core classes and functions
__all__ = [
    # Setup
    "setup",
    "is_valid",
    "is_free_tier",
    # Feature checking
    "has_feature",
    "get_feature_config",
    "get_feature_limit",
    # License info
    "get_instance_id",
    "get_license_source",
    "get_validation_result",
    "get_expiry_timestamp",
    "to_dict",
    # Core classes (for advanced use)
    "License",
    "LicenseManager",
    "LicenseValidationResult",
    "generate_keypair",
    "sign_license",
    # Exceptions
    "LicenseError",
    "InvalidLicenseError",
    "SignatureVerificationError",
    "ExpiredLicenseError",
    "FeatureNotFoundError",
    # License management
    "apply_license_from_base64",
    "reload_license",
    "validate_license_payload",
    # Constants
    "DEFAULT_LICENSE_PATH",
    "LICENSE_ENV_VAR",
]
