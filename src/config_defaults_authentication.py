"""
Authentication defaults extracted from :mod:`src.config_defaults`.

Keeps ``config_defaults.py`` under 1000 lines; imported back so the public
``get_default_config`` contract is unchanged.
"""

from pathlib import Path
from typing import Any, Dict


def get_authentication_defaults(home_dir: Path) -> Dict[str, Any]:
    """Return the default ``authentication`` configuration block."""
    return {
        "encryption": {
            # SECURITY: Enforce TPM or Environment Variable key source for production
            "require_secure_source": True,
            # Media encryption key (Base64 encoded 32-byte key)
            # If not set, media encryption will be disabled
            "media_key": "${PLEXICHAT_MEDIA_KEY:-}",
        },
        "account_deletion": {
            "enabled": True,
            "grace_period_days": 30,
            "reminder_days_before_purge": [7, 1],
            "hard_freeze": True,
            "anonymize_content": True,
            "audit_log": {
                # Path to the deletion audit log (JSONL file with hash chain)
                "file_path": str(home_dir / "data" / "deletion_log.jsonl"),
                # Enable SHA256 hash chaining for tamper-evidence
                "hash_chain_enabled": True,
                # Backup audit log to S3 for disaster recovery
                "backup_to_s3": True,
                "s3_backup_path": "audit/deletions/log_backup.jsonl",
                # If True, server refuses to start when audit log integrity check fails.
                # Disable only if you need to recover from a backup or rebuild the chain.
                "halt_on_invalid_audit": True,
                # SECURITY: when True and the chain fails verification
                # at boot, ``verify_chain`` renames the broken file to
                # ``<path>.broken-<ts>`` so the next ``log_event`` call
                # can begin a fresh genesis chain.  The sidecar keeps
                # the broken GDPR records for inventory / forensic
                # review without blocking legitimate new entries.
                # Operators MUST audit sidecar files manually.
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
            "age_verification_type": "boolean",  # "boolean" or "dob"
        },
        "dsar": {
            # Data Subject Access Request (GDPR Article 20 - Right to Portability)
            "enabled": True,
            # Require admin review before generating exports (vs auto-approve)
            "require_admin_review": True,
            # Default export format
            "default_format": "json",
            # Supported export formats
            "export_formats": ["json", "zip"],
            # Maximum export size in MB (approximate limit, collection is unconstrained)
            "max_export_size_mb": 500,
            # How long download links remain valid after export is ready (days)
            "retention_days": 7,
            # How long pending requests survive without admin action (days)
            "pending_expiry_days": 30,
            # Local directory for export files when using the local storage
            # backend. The backend itself, S3 credentials, and database
            # storage settings are all inherited from the `media` block to
            # avoid duplicating configuration.
            "local_path": str(home_dir / "data" / "exports" / "dsar"),
            # Audit log settings (mirrors account_deletion audit_log structure)
            "audit_log": {
                "file_path": str(home_dir / "data" / "dsar_audit_log.jsonl"),
                "hash_chain_enabled": True,
                "backup_to_s3": True,
                "s3_backup_path": "audit/dsar/log_backup.jsonl",
                "halt_on_invalid_audit": True,
                # SECURITY: when True and the chain fails verification
                # at boot, ``verify_chain`` renames the broken file
                # to ``<path>.broken-<ts>`` so the harvester can
                # continue writing against a fresh genesis chain
                # instead of being blocked.  GDPR-impacting
                # artefacts still live in the sidecar.
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
            # Per-device-trust session durations. Sessions created on a
            # trusted device (see auth_devices.trusted) may live longer than
            # the default: trusted devices already passed 2FA at trust time,
            # so longer-lived sessions carry less risk there. Set
            # trusted_expire_hours to null (or omit it) to disable the
            # override and always use expire_hours.
            "trusted_expire_hours": 720,
            # When extend_on_activity re-arms expiry, trusted-device sessions
            # extend to this duration instead of expire_hours (same null
            # semantics). Falls back to trusted_expire_hours, then
            # expire_hours.
            "trusted_extend_threshold_hours": 168,
        },
        "reset_password": {
            # Password-reset emails deliver a deep link instead of a bare
            # token. Set client_url to the web client's base origin (e.g.
            # https://chat.example.com) so the link is fully qualified;
            # without it the link is relative to the web root, which still
            # avoids requiring users to paste raw tokens into a form.
            "client_url": "",
            "path": "/reset-password",
        },
        "security": {
            "max_failed_attempts": 5,
            "lockout_duration_minutes": 15,
            # Account lockout requires failures from at least this many
            # distinct source IPs within failed_login_window_minutes, so a
            # single host cannot deny service to a known username.
            "lockout_min_distinct_ips": 2,
            "failed_login_window_minutes": 15,
            "token_cache_ttl": 30,
            "token_verify_rate_limit": 100,
            "token_binding": False,
            # HttpOnly session cookie Secure flag. SECURE BY DEFAULT;
            # explicitly set false ONLY for plain-HTTP localhost/dev
            # deployments (production behind TLS needs no override).
            "session_cookie_secure": True,
        },
        "totp": {
            "issuer": "Plexichat",
            "digits": 6,
            "interval": 30,
            "backup_code_count": 10,
            # Minimum 8 enforced in totp.generate_backup_codes(); lower values
            # are clamped. 32 bits (~10/2^32 per 10 codes) with per-challenge
            # and per-user limiters. Configure higher for more entropy.
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
