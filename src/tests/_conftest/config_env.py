"""Import-time test environment setup (config, logger, validator shims)."""

import os
import tempfile

import utils.config as config
import utils.version as version

# Notification fan-out (send-path mention notifications) runs on a background
# consumer in production. Under pytest it must complete inline so tests can
# assert on notifications immediately after send_message returns.
os.environ.setdefault("PLEXICHAT_SYNC_NOTIFICATION_DISPATCH", "1")


DEFAULT_TEST_CONFIG = {
    "authentication": {
        "accounts": {
            "allow_registration": True,
            "require_email_verification": False,
            "max_bots_per_user": 5,
            "username_min_length": 3,
            "username_max_length": 32,
        },
        "sessions": {
            "token_bytes": 32,
            "expire_hours": 168,
            "max_per_user": 10,
            "extend_on_activity": True,
            "extend_threshold_hours": 24,
        },
        "totp": {"backup_code_count": 10, "issuer": "TestApp"},
        "security": {
            "max_failed_attempts": 3,
            "lockout_duration_minutes": 1,
            # Dev/localhost override: the TestClient speaks plain HTTP, and
            # Secure cookies would never be sent back over http://testserver.
            "session_cookie_secure": False,
        },
        "password": {
            "min_length": 12,
            "max_length": 128,
            "require_uppercase": True,
            "require_lowercase": True,
            "require_digit": True,
            "require_special": True,
        },
        # Disable strict email TLD validation in tests so the
        # fixture pool's ``...@test.local`` (RFC 6761 reserved)
        # addresses always pass ``auth.passwords.validate_email``.
        "email_validation": {"strict": False},
    },
    # Test-friendly Argon2id cost parameters. AuthManager reads these via
    # config and computes a real dummy hash on every ``auth.setup()`` plus a
    # real hash on every ``register()`` (``self.crypto.hash_password`` — NOT
    # the module function the db fixture patches). With production defaults
    # (time_cost=3, memory_cost=64MB) each of those costs ~70ms — thousands
    # of tests paid hundreds of seconds of real Argon2 work. The real-hashing
    # tests construct their own EncryptionManager explicitly and are
    # unaffected; every generated hash still carries the ``$argon2id$`` PHC
    # prefix so format assertions hold.
    "encryption": {
        "argon2": {
            "time_cost": 1,
            "memory_cost": 8,
            "parallelism": 1,
            "hash_length": 32,
            "salt_length": 16,
        },
    },
    "messaging": {
        "max_message_length": 4000,
        "max_attachments": 10,
        "max_attachments_per_message": 10,
    },
    "media": {"compression": {"enabled": False}},
    "applications": {"max_applications_per_user": 1000},
    "ratelimit": {
        "enabled": True,
        "global": {"limit": 100, "window": 60},
        "user": {"limit": 5, "window": 60},
        "ip": {"limit": 10, "window": 60},
    },
    "rate_limiting": {
        "enabled": True,
        "admin_bypass": True,
        "internal_bypass": True,
        "global": {"requests": 50, "window_seconds": 1.0, "burst": 10},
        "user": {
            "requests": 70,
            "window_seconds": 60.0,
            "burst": 20,
            "hourly_limit": 3600,
            "daily_limit": 50000,
        },
        "ip": {
            "requests": 70,
            "window_seconds": 60.0,
            "burst": 10,
            "hourly_limit": 1800,
            "daily_limit": 10000,
        },
        "routes": {"send_message": {"requests": 5, "window": 5.0}},
        "bot_multiplier": 1.5,
        "webhook_multiplier": 1.0,
    },
    "api": {
        "cors_origins": ["http://testserver", "http://localhost:3000"],
        "allow_wildcard_cors": True,
        "cors_allow_headers": [
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "Accept",
            "Origin",
            "X-Custom-Header",
        ],
    },
    "version": {"current": "r.1.0-1", "min_client": "a.1.0-1"},
    "servers": {"templates": {"max_templates_per_user": 1000}},
    # Discovery tests build small servers (owner + 1-2 members) and
    # bump immediately after listing; keep the directory thresholds
    # test-friendly (production defaults: 10 members / 4h cooldown).
    "search": {"discovery": {"min_members_for_listing": 3, "bump_cooldown_hours": 0}},
}

# =============================================================================
# Setup Config at Import Time (Before Any Test Collection)
# =============================================================================

# Setup config immediately at import time to avoid import errors
_test_config_dir = tempfile.mkdtemp()
_test_config_path = os.path.join(_test_config_dir, "config.yaml")
config.setup(config_path=_test_config_path, default_config=DEFAULT_TEST_CONFIG)
version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")

# Initialize logger for tests
import utils.logger as logger  # noqa: E402

_test_log_dir = tempfile.mkdtemp()
try:
    logger.setup(log_dir=_test_log_dir, level="WARNING", zip_logs=False)
except Exception:
    pass

# Patch Database to add missing fetch_last_insert_id method for migration tracker
from src.core.database import Database  # noqa: E402

if not hasattr(Database, "fetch_last_insert_id"):

    def fetch_last_insert_id(self):
        """Get the last insert ID (compatibility shim for migration tracker)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT last_insert_rowid()")
        result = cursor.fetchone()
        return result[0] if result else None

    Database.fetch_last_insert_id = fetch_last_insert_id


# =============================================================================
# Test-only validator bypass (module-level, scoped to test session)
# -----------------------------------------------------------------------------
# The authentication module's ``validate_email`` performs strict
# validation.  Every fixture that creates users via ``auth.register()``
# would hit ``raise InvalidEmailError`` for test-generated ``@test``
# / ``@test.local`` addresses -- breaking settings/polls/soundboard/
# stickers suites.  We rebind the validators at import time.  This is
# scoped to the test conftest and reverts when pytest's interpreter
# exits; the production runtime is never touched.
#
# Two layers:
#   1. The real validator runs first (so genuine bug-detection still
#      kicks in for real-world addresses like ``bad@x.x``).
#   2. A blanket whitelist catches every RFC 6761 reserved TLD
#      (``.test``/``.local``/``.example``/``.invalid``). The bare ``@test``
#      host is deliberately NOT whitelisted: ``user@test`` has no TLD and
#      must be rejected by the strict validator (the security suite asserts
#      this). Fixture emails use ``@test.local``, which ``.local`` covers.
from src.core.auth import passwords as _auth_passwords  # noqa: E402
from src.core.auth.managers import registration as _reg_module  # noqa: E402

_TEST_TLD_WHITELIST = (
    ".test",
    ".local",
    ".example",
    ".invalid",
)
_original_validate_email = _auth_passwords.validate_email


def address_whitelisted(email: str) -> bool:
    """True when the email ends in any reserved TLD or uses ``@test``."""
    if not email or "@" not in email:
        return False
    host = email.split("@", 1)[1].lower()
    return any(
        host == suffix.lstrip("@") or host.endswith(suffix)
        for suffix in _TEST_TLD_WHITELIST
    )


def _test_always_valid_email(email, _orig=_original_validate_email):
    """Bypass: real validator first; whitelist as final fallback."""
    if email and "@" in email and not any(c.isspace() for c in email):
        if _orig(email):
            return True
        return address_whitelisted(email)
    return False


# Apply the email-only test accommodation at module import time so every
# fixture sees it without depending on autouse-fixture ordering. Username
# validation deliberately remains the production implementation: globally
# bypassing it made the property-based security tests test the fixture rather
# than the validator.
_auth_passwords.validate_email = _test_always_valid_email
_reg_module.validate_email = _test_always_valid_email
_reg_module.validate_username = _auth_passwords.validate_username
