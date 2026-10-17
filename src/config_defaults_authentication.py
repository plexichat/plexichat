"""Authentication configuration defaults.

Keeps ``config_defaults.py`` under 1000 lines. Imported back so the public
``get_default_config`` contract is unchanged.
"""

from pathlib import Path
from typing import Any, Dict


def get_authentication_defaults(home_dir: Path) -> Dict[str, Any]:
    """Return the default ``authentication`` configuration block.

    Args:
        home_dir: The application home directory, used as the base path for
            log files and export storage.

    Returns:
        A dictionary containing all authentication configuration defaults.
    """
    return {
        "encryption": {
            "require_secure_source": True,
            "media_key": "${PLEXICHAT_MEDIA_KEY:-}",
        },
        "account_deletion": {
            "enabled": True,
            "grace_period_days": 30,
            "reminder_days_before_purge": [7, 1],
            "hard_freeze": True,
            "anonymize_content": True,
            "audit_log": {
                "file_path": str(home_dir / "data" / "deletion_log.jsonl"),
                "hash_chain_enabled": True,
                "backup_to_s3": True,
                "s3_backup_path": "audit/deletions/log_backup.jsonl",
                "halt_on_invalid_audit": True,
                "rotate_on_broken_chain": False,
            },
            "reaper": {
                "interval_hours": 24,
                "boot_check_enabled": True,
                "batch_size": 50,
            },
        },
        "accounts": {
            "allow_registration": True,
            "require_email_verification": False,
            "max_bots_per_user": 5,
            "username_min_length": 3,
            "username_max_length": 32,
            "age_gate_enabled": False,
            "minimum_age": 13,
            "age_verification_type": "boolean",
        },
        "dsar": {
            "enabled": True,
            "require_admin_review": True,
            "default_format": "json",
            "export_formats": ["json", "zip"],
            "max_export_size_mb": 500,
            "retention_days": 7,
            "pending_expiry_days": 30,
            "local_path": str(home_dir / "data" / "exports" / "dsar"),
            "audit_log": {
                "file_path": str(home_dir / "data" / "dsar_audit_log.jsonl"),
                "hash_chain_enabled": True,
                "backup_to_s3": True,
                "s3_backup_path": "audit/dsar/log_backup.jsonl",
                "halt_on_invalid_audit": True,
                "rotate_on_broken_chain": False,
            },
            "harvester": {
                "interval_hours": 24,
                "boot_check_enabled": True,
                "batch_size": 20,
            },
        },
        "email_validation": {
            "strict": True,
            "allow_custom_tlds": False,
            "valid_tlds": [],
        },
        "sessions": {
            "token_bytes": 32,
            "expire_hours": 168,
            "max_per_user": 10,
            "extend_on_activity": True,
            "extend_threshold_hours": 24,
            "trusted_expire_hours": 720,
            "trusted_extend_threshold_hours": 168,
        },
        "reset_password": {
            "client_url": "",
            "path": "/reset-password",
        },
        "security": {
            "max_failed_attempts": 5,
            "lockout_duration_minutes": 15,
            "lockout_min_distinct_ips": 2,
            "failed_login_window_minutes": 15,
            "token_cache_ttl": 30,
            "token_verify_rate_limit": 100,
            "token_binding": False,
            "session_cookie_secure": True,
        },
        "totp": {
            "issuer": "Plexichat",
            "digits": 6,
            "interval": 30,
            "backup_code_count": 10,
            "backup_code_length": 8,
        },
        "password": {
            "min_length": 12,
            "max_length": 128,
            "require_uppercase": True,
            "require_lowercase": True,
            "require_digit": True,
            "require_special": True,
            "guidance_url": "/docs/api/end-user/password-guidance.md",
        },
        "passkeys": {
            "enabled": True,
            "rp_name": "Plexichat",
            "rp_id": "${PASSKEY_RP_ID:-localhost}",
            "origin": "${PASSKEY_ORIGIN:-http://localhost}",
            "challenge_ttl_seconds": 300,
            "cleanup_interval_hours": 24,
        },
        "bots": {"token_bytes": 48, "require_owner_2fa": False},
    }
