"""
Default configuration for the Plexichat Server.
This file provides the baseline settings used when no configuration file is found.
"""

import secrets
from pathlib import Path
from typing import Dict, Any

# Version should be updated in main.py, this is a fallback
DEFAULT_VERSION = "a.1.0-103"

# License feature flags used by the Artifacts system.
# Checked at runtime via utils.licensing.has_feature(<name>).
ARTIFACTS_LICENSE_FEATURES = {
    "artifacts": "Master artifacts feature (any artifact type).",
    "artifacts_whiteboard": "Licensed multi-user live whiteboard artifacts.",
    "voice_transcription": "Licensed automatic voice-call transcription.",
}


def get_default_config(version: str = DEFAULT_VERSION) -> Dict[str, Any]:
    """
    Returns the default configuration dictionary for Plexichat.

    Args:
        version: The current application version string.

    Returns:
        A dictionary containing all default configuration keys and values.
    """
    home_dir = Path.home() / ".plexichat"

    return {
        "application": {
            "name": "Plexichat",
            "version": version,
            "environment": "development",
        },
        "server": {"host": "127.0.0.1", "port": 8000, "workers": 1, "reload": False},
        "logging": {
            "level": "DEBUG",
            "max_bytes": 10485760,  # 10MB
            "backup_count": 5,
            "zip_logs": True,
            "rotate": True,
            # SECURITY WARNING: Set to False in production to avoid leaking sensitive info
            "include_exception_details": False,
        },
        "database": {
            "type": "sqlite",
            "path": str(home_dir / "data" / "plexichat.db"),
            "postgres": {
                "host": "${POSTGRES_HOST:-localhost}",
                "port": 5432,
                "user": "${POSTGRES_USER:-postgres}",
                "password": "${POSTGRES_PASSWORD:-}",
                "dbname": "${POSTGRES_DBNAME:-plexichat}",
                "sslmode": "${POSTGRES_SSLMODE:-prefer}",
            },
            "connection_pool": {
                "min_connections": 5,
                "max_connections": 100,
                "connect_timeout": 10,
            },
            "monitoring": {
                "slow_query_threshold_ms": 1000,
                "alert_on_slow_queries": True,
            },
            "migrations": {
                "auto_migrate": True,
                "migration_dir": str(home_dir / "migrations"),
                "irreversible_migration_delay_days": 7,
            },
        },
        "redis": {
            "enabled": False,
            "host": "${REDIS_HOST:-localhost}",
            "port": 6379,
            "password": "${REDIS_PASSWORD:-}",
            "db": 0,
            "ssl": False,
            "key_prefix": "plexichat:",
            "connection_pool": {"max_connections": 50, "timeout": 5},
            "ttl": {"session": 1800, "presence": 300, "cache": 60},
            "cache_max_items": 1000,
        },
        "authentication": {
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
            },
            "security": {
                "max_failed_attempts": 5,
                "lockout_duration_minutes": 15,
                "token_cache_ttl": 30,
                "token_verify_rate_limit": 100,
                "token_binding": False,
            },
            "totp": {
                "issuer": "Plexichat",
                "digits": 6,
                "interval": 30,
                "backup_code_count": 10,
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
        },
        "api": {
            "title": "Plexichat API",
            "description": "REST API for the Plexichat messaging platform",
            "version": version,
            "api_prefix": "/api/v1",
            "debug": True,
            "cors_origins": [
                "http://localhost:5000",
                "http://127.0.0.1:5000",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
                "https://plexichat.com",
                "https://app.plexichat.com",
                "https://api.plexichat.com",
                "http://localhost:8443",
            ],
            "cors_allow_credentials": True,
            "cors_allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "cors_allow_headers": [
                "Authorization",
                "Content-Type",
                "X-Requested-With",
                "Accept",
                "Origin",
                "X-API-Access-Token",
                "X-Plexichat-Request",
                "X-RateLimit-Bypass",
            ],
            "trusted_proxies": [],
            "trust_x_forwarded_for": False,
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        },
        "websocket": {
            "heartbeat_interval_ms": 45000,
            "session_timeout_ms": 60000,
            "max_connections_per_user": 5,
            "rate_limit_per_minute": 120,
            "max_message_size": 65536,  # 64KB
            "max_decompressed_size": 262144,  # 256KB
            "compression_enabled": True,
            "allowed_origins": [],
        },
        "messaging": {
            "encrypt_messages": True,
            "encrypt_attachments": True,
            "max_message_length": 4000,
            "max_group_participants": 100,
            "max_attachment_size": 10485760,  # 10MB
            "max_attachments_per_message": 10,
            "dm_auto_create": True,
            "message_preview_length": 100,
        },
        "media": {
            "data_dir": str(home_dir / "data"),
            "logs_dir": str(home_dir / "logs"),
            "media_dir": str(home_dir / "media"),
            "temp_dir": str(home_dir / "temp"),
            "storage_backend": "local",  # "local", "s3", or "database"
            "encrypt_at_rest": True,
            "local_path": str(home_dir / "media"),
            "local_url": "/media",
            "s3_bucket": "${S3_BUCKET:-}",
            "s3_access_key": "${S3_ACCESS_KEY:-}",
            "s3_secret_key": "${S3_SECRET_KEY:-}",
            "s3_region": "${S3_REGION:-us-east-1}",
            "s3_endpoint": "${S3_ENDPOINT:-}",
            "s3_public_url": "${S3_PUBLIC_URL:-}",
            "database_url": "/api/v1/media/blob",
            "database_max_size": 524288,  # 512KB
            "auto_route_to_database": {
                "enabled": True,
                "max_size": 524288,
                "content_types": [
                    "text/plain",
                    "application/json",
                    "text/markdown",
                    "text/csv",
                ],
            },
            "size_limits": {
                "image": 10485760,
                "video": 104857600,
                "audio": 52428800,
                "document": 26214400,
                "icon": 2097152,
                "avatar": 5242880,
                "other": 10485760,
            },
            "allowed_types": {
                "image": ["image/jpeg", "image/png", "image/gif", "image/webp"],
                "video": ["video/mp4", "video/webm", "video/quicktime"],
                "audio": ["audio/mpeg", "audio/ogg", "audio/wav", "audio/webm"],
                "document": [
                    "application/pdf",
                    "text/plain",
                    "application/zip",
                    "text/markdown",
                    "application/json",
                ],
            },
            "thumbnail_sizes": [64, 128, 256, 512],
            "image_quality": 85,
            "image_optimize": True,
            "image_processing": {
                "max_dimension": 16384,
                "max_pixels": 178956970,
                "max_thumbnail_requests_per_minute": 60,
            },
            "video_processing": {
                "ffprobe_timeout": 30,
                "max_size_for_metadata": 524288000,
            },
            "signing_key": "CHANGE_THIS_SIGNING_KEY",
            "signing_expiry": 3600,
            "scanner_enabled": False,
            "scanner_host": "localhost",
            "scanner_port": 3310,
            "proxy_enabled": True,
            "proxy_cache_ttl": 86400,
            "proxy_max_size": 10485760,
            "proxy_buffer_size": 65536,
            "rate_limit": {
                "enabled": True,
                "uploads_per_minute": 10,
                "uploads_per_hour": 100,
                "max_total_size_per_day": 536870912,  # 512MB
            },
            "phash": {
                "enabled": True,
                "algorithm": "phash",
                "hash_size": 8,
                "similarity_threshold": 10,
                "highfreq_factor": 4,
            },
            "deduplication": {
                "enabled": True,
                "hash_algorithm": "sha256",
                "min_size": 10240,
                "auto_block_threshold": 5,
            },
        },
        "rate_limiting": {
            "enabled": True,
            "global": {"requests": 25000, "window_seconds": 60.0, "burst": 5000},
            "user": {
                "requests": 10000,
                "window_seconds": 60.0,
                "burst": 2500,
                "hourly_limit": 300000,
                "daily_limit": 5000000,
            },
            "ip": {
                "requests": 5000,
                "window_seconds": 60.0,
                "burst": 1500,
                "hourly_limit": 150000,
                "daily_limit": 1000000,
            },
            "routes": {
                "static_client_html": {
                    "requests": 300,
                    "window_seconds": 60.0,
                    "burst": 100,
                    "hourly_limit": 6000,
                },
                "static_client_assets": {
                    "requests": 5000,
                    "window_seconds": 60.0,
                    "burst": 1000,
                    "hourly_limit": 180000,
                },
            },
            "bot_multiplier": 1.5,
            "webhook_multiplier": 1.0,
            "admin_bypass": True,
            "internal_bypass": True,
            "bypass_secret": secrets.token_hex(32),
        },
        "servers": {
            "server_name_min_length": 2,
            "server_name_max_length": 100,
            "channel_name_max_length": 100,
            "role_name_max_length": 100,
            "invite_code_length": 12,
            "events": {
                "max_event_duration_hours": 168,
                "max_recurring_instances": 50,
            },
            "onboarding": {
                "max_onboarding_steps": 10,
                "max_welcome_channels": 5,
                "max_step_options": 25,
            },
            "templates": {
                "template_code_length": 8,
                "max_channels_in_template": 100,
                "max_roles_in_template": 50,
                "max_templates_per_user": 25,
            },
        },
        "user_features": {
            "alpha_registration_enabled": True,
            "default_tier": "standard",
            "badge_display_limit": 5,
            "available_badges": [
                "alpha_tester",
                "early_supporter",
                "staff",
                "verified",
                "bug_hunter",
                "contributor",
                "moderator",
                "partner",
            ],
            "rate_limit_tiers": {
                "standard": {
                    "multiplier": 1.0,
                    "max_voice_minutes_per_day": 120,
                    "max_video_minutes_per_day": 60,
                    "max_file_uploads_per_day": 50,
                    "max_file_size_mb": 50,
                    "max_servers": 100,
                    "max_message_length": 2000,
                    "max_reactions_per_message": 20,
                    "max_pins_per_channel": 50,
                    "custom_emoji_slots": 50,
                },
                "alpha": {
                    "multiplier": 2.0,
                    "max_voice_minutes_per_day": 480,
                    "max_video_minutes_per_day": 240,
                    "max_file_uploads_per_day": 200,
                    "max_file_size_mb": 25,
                    "max_servers": 200,
                    "max_message_length": 4000,
                    "max_reactions_per_message": 50,
                    "max_pins_per_channel": 100,
                    "custom_emoji_slots": 100,
                },
                "premium": {
                    "multiplier": 3.0,
                    "max_voice_minutes_per_day": -1,
                    "max_video_minutes_per_day": -1,
                    "max_file_uploads_per_day": 500,
                    "max_file_size_mb": 100,
                    "max_servers": 500,
                    "max_message_length": 4000,
                    "max_reactions_per_message": 100,
                    "max_pins_per_channel": 200,
                    "custom_emoji_slots": 250,
                },
                "staff": {
                    "multiplier": 10.0,
                    "max_voice_minutes_per_day": -1,
                    "max_video_minutes_per_day": -1,
                    "max_file_uploads_per_day": -1,
                    "max_file_size_mb": 500,
                    "max_servers": -1,
                    "max_message_length": 8000,
                    "max_reactions_per_message": -1,
                    "max_pins_per_channel": -1,
                    "custom_emoji_slots": -1,
                },
            },
            "admin_rate_limit": {"max_per_minute": 30, "max_per_hour": 200},
        },
        "voice": {
            "enabled": True,
            "sfu_backend": "mediasoup",
            "mediasoup_url": "https://localhost:4443",
            "janus_url": "http://localhost:8088/janus",
            "stun_urls": [
                "stun:stun.l.google.com:19302",
                "stun:stun1.l.google.com:19302",
                "stun:stun2.l.google.com:19302",
                "stun:stun3.l.google.com:19302",
            ],
            "turn_urls": [],
            "turn_username": "",
            "turn_credential": "",
            "turn_secret": "",
            "turn_ttl": 86400,
            "log_connections": False,
        },
        "search": {
            "enabled": True,
            "backend": "sqlite_fts5",
            "result_limit": 100,
            "batch_size": 100,
            "write_time_indexing": True,
            "discovery": {
                "enabled": True,
                "min_members_for_listing": 10,
                "max_tags": 10,
                "bump_cooldown_hours": 4,
            },
        },
        "oauth": {
            "state_ttl_seconds": 600,
            "state_token_bytes": 32,
            "nonce_token_bytes": 32,
            "cleanup_on_verify": True,
            "max_states_per_ip": 10,
            "pkce_enabled": True,
            "pkce": {
                "verifier_length": 64,
                "min_verifier_length": 43,
                "max_verifier_length": 128,
            },
            "google": {"client_id": "", "client_secret": ""},
            "github": {"client_id": "", "client_secret": ""},
            "microsoft": {"client_id": "", "client_secret": ""},
        },
        "encryption": {
            "argon2": {
                "time_cost": 3,
                "memory_cost": 65536,
                "parallelism": 2,
                "hash_length": 32,
                "salt_length": 16,
            },
            "aes_gcm": {"key_length": 32, "nonce_length": 12, "tag_length": 16},
            "snowflake": {
                "epoch": "2024-01-01T00:00:00Z",
                "worker_id": None,
                "datacenter_id": None,
            },
            "key_rotation_days": 180,  # 6 months, configurable via encryption.key_rotation_days
            "hsm": {
                "enabled": False,
                "library_path": "/usr/lib/softhsm/libsofthsm2.so",
                "slot_id": 0,
                "pin": "",
                "key_label": "plexichat_kek",
            },
        },
        "monitoring": {
            "enabled": True,
            "log_interval": 300,
            "metrics_enabled": True,
            "alert_thresholds": {
                "cpu_percent": 80,
                "memory_percent": 85,
                "db_pool_saturation_percent": 75,
                "query_time_ms": 5000,
                "db_errors_per_minute": 10,
                "api_response_time_ms": 2000,
                "error_rate_percent": 5,
                "active_connections": 1000,
            },
        },
        "selftest": {
            "enabled": False,
            "run_on_startup": False,
            "exit_on_failure": False,
            "capture_stack_traces": True,
            "retry_on_failure": True,
            # Controls whether admin-only endpoints are tested during self-test.
            # When False, admin endpoints are skipped entirely and the test user
            # is NOT granted admin permissions. Set to True to test admin routes
            # (requires admin-level permissions on the test user).
            "enable_admin_tests": True,
            "excluded_endpoints": ["/api/v1/auth/logout", "/api/v1/admin/logout"],
            "test_user": {
                "username": "selftest_admin",
                "email": "selftest@plexichat.com",
                "password": None,  # pragma: allowlist secret
            },
        },
        "admin_ui": {
            "enabled": True,
            "path": "/admin",
            "require_otp": True,
            "force_password_change_first_login": True,
            "session_timeout_minutes": 480,
            "max_concurrent_sessions": 3,
            "host_restriction": {
                "enabled": True,
                "allowed_hosts": ["127.0.0.1", "localhost", "::1"],
            },
            "blocked_ips": [],
            "allowed_origins": [],
            "rate_limit": {
                "max_attempts": 5,
                "window_seconds": 300,
                "lockout_seconds": 900,
            },
            "rbac": {
                "enabled": True,
                "default_role": "super_admin",
            },
            "approval_workflows": {
                "enabled": True,
                "single_admin_bypass": True,
                "require_approval_for": [
                    "users.force_purge",
                    "users.delete",
                    "servers.delete",
                ],
                "approval_required_admins": 2,
                "approval_timeout_hours": 48,
                "auto_approve_after_hours": 72,
            },
            "audit": {
                "log_to_file": True,
                "log_to_database": True,
                "sensitive_actions_always_db": True,
                "retention_days": 365,
            },
            "notifications": {
                "email_on_critical_actions": False,
                "email_on_approval_required": False,
                "webhook_url": "",
            },
            "security": {
                "password_policy": {
                    "min_length": 12,
                    "require_uppercase": True,
                    "require_lowercase": True,
                    "require_numbers": True,
                    "require_special_chars": True,
                    "prevent_common_passwords": True,
                },
                "session": {
                    "max_concurrent_sessions": 3,
                    "timeout_idle_minutes": 30,
                },
            },
        },
        "tls": {
            "enabled": False,
            "auto_generate_self_signed": False,
            "cert_path": str(home_dir / "certs" / "server.crt"),
            "key_path": str(home_dir / "certs" / "server.key"),
            "cert_days": 365,
        },
        "email": {
            "smtp_host": "localhost",
            "smtp_port": 587,
            "smtp_user": "",
            "from_email": "noreply@plexichat.internal",
            "use_tls": True,
        },
        "presence": {
            "typing_timeout_ms": 10000,
            "timeout_ms": 300000,
            "update_interval_ms": 60000,
        },
        "polls": {
            "max_options": 10,
            "min_options": 2,
            "max_question_length": 300,
            "max_option_length": 100,
            "min_duration_hours": 1,
            "max_duration_hours": 168,
        },
        "emojis": {
            "max_emojis_per_server": 50,
            "max_animated_emojis_per_server": 50,
            "max_emoji_size": 262144,
            "emoji_min_name_length": 2,
            "emoji_max_name_length": 32,
            "allowed_formats": ["image/png", "image/jpeg", "image/gif", "image/webp"],
        },
        "stickers": {
            "max_packs_per_server": 50,
            "max_stickers_per_pack": 100,
            "max_sticker_size": 524288,
            "max_sticker_name_length": 32,
            "max_pack_name_length": 64,
            "max_pack_description_length": 256,
            "allowed_formats": ["png", "apng", "json"],
            "max_suggestions": 10,
        },
        "soundboard": {
            "max_sounds_per_server": 50,
            "max_sound_size": 2097152,
            "max_sound_duration_seconds": 10,
            "max_sound_name_length": 64,
            "allowed_formats": ["audio/mpeg", "audio/ogg", "audio/wav", "audio/webm"],
            "default_cooldown_seconds": 3.0,
            "max_cooldown_seconds": 30.0,
        },
        "webhooks": {
            "max_webhooks_per_channel": 100,
            "max_webhooks_per_server": 50,
            "max_message_length": 2000,
            "max_embeds_per_message": 10,
        },
        "automod": {
            "enabled": True,
            "exempt_owners": True,
            "exempt_admins": True,
            "rules": {
                "caps": {
                    "enabled": True,
                    "max_percentage": 70.0,
                    "min_length": 10,
                    "ignore_commands": True,
                }
            },
        },
        "applications": {
            "max_applications_per_user": 25,
            "max_commands_per_app": 100,
            "interaction_timeout": 900,
            "webhook_signature_secret": secrets.token_hex(32),
            "oauth": {
                "code_expiry_seconds": 600,
                "token_expiry_seconds": 604800,
                "refresh_enabled": True,
            },
            "rate_limits": {
                "requests_per_minute": 60,
            },
        },
        # License gating (checked via utils.licensing.has_feature):
        #   - the whole "artifacts" block is gated by the "artifacts" feature
        #   - "artifacts.whiteboard" (and its licensed_feature key) is gated
        #     by the "artifacts_whiteboard" feature
        #   - "artifacts.voice.transcription" is gated by the
        #     "voice_transcription" feature
        # See ARTIFACTS_LICENSE_FEATURES for the full documented set.
        "artifacts": {
            "enabled": True,
            "default_retention_days": None,  # None = artifacts never expire by default
            "allow_per_server_override": True,
            "max_artifact_size_mb": 200,
            "editor": {
                "enabled": True,
                "allowed_languages": [
                    "python",
                    "javascript",
                    "typescript",
                    "json",
                    "markdown",
                    "go",
                    "rust",
                    "sql",
                    "yaml",
                    "html",
                    "css",
                ],
                "max_file_size_mb": 50,
            },
            "whiteboard": {
                "enabled": False,
                "licensed_feature": "artifacts_whiteboard",
                "max_participants": 50,
                "persist_ops": True,
                "op_rate_per_sec": 30,
            },
            "voice": {
                "allow_recording": True,
                "transcription": {
                    "provider": "local_whisper",  # default; active only when enabled:True AND whisper present
                    "enabled": False,  # OFF until explicitly configured
                    "auto_transcribe": False,
                    "language": "auto",
                    "diarize": False,
                    "model_size": "base",
                    "whisper_probe_on_startup": True,
                    "openai_api_key": "${OPENAI_API_KEY:-}",
                    "azure_key": "${AZURE_SPEECH_KEY:-}",
                    "max_audio_minutes": 120,
                },
                "transcript_retention_days": None,
            },
            "retention": {
                "run_cleanup_interval_minutes": 60,
                "purge_expired": True,
            },
        },
        "bots": {
            "enabled": True,
            "max_per_server": 10,
            "max_per_server_premium": 50,
            "allow_custom_bots": False,
            "allow_custom_bots_premium": True,
            "manage_bots_role": "admin",
            "request_bot_enabled": True,
            "curated_bots_only": False,
            "webhook_retention_days": 30,
            "interaction_timeout": 900,
            "slash_commands_enabled": True,
            "oauth_consent_required": True,
            "bot_avatar_max_size": 512,
            "bot_avatar_max_file_size": 5242880,
            "bot_avatar_allowed_types": [
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
            ],
            "rate_limits": {
                "requests_per_minute": 30,
                "burst_limit": 10,
            },
            "approved_bots_page_size": 50,
            "max_requests_pending_per_user": 10,
        },
        "versioning": {"min_supported_version": version, "update_url": None},
        "docs": {
            "enabled": True,
            "path": "/docs/api",
            "title": "Plexichat API Documentation",
            "description": "Runtime documentation for the Plexichat backend",
            "base_url": "https://your-plexichat-host.example/api/v1",
            "websocket_url": "wss://your-plexichat-host.example/gateway",
            "theme": {
                "style": "dark",
                "primary_color": "#6366f1",
                "primary_dark_color": "#4f46e5",
                "background_color": "#0b0f19",
                "surface_color": "#111827",
                "code_background": "#0f172a",
                "text_color": "#f9fafb",
                "muted_color": "#9ca3af",
                "accent_color": "#10b981",
                "warning_color": "#f59e0b",
                "error_color": "#ef4444",
                "border_color": "#1f2937",
                "font_family": "'JetBrains Mono', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
                "code_font": "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
            },
            "rate_limit": {"enabled": True, "requests": 60, "window_seconds": 60},
            "cache": {"enabled": True, "ttl_seconds": 300},
            "security": {"require_auth": False},
        },
        "feedback": {
            "enabled": True,
            "rate_limit": {"max_per_hour": 5, "max_per_day": 20},
        },
        "reports": {
            "enabled": True,
        },
        "avatars": {
            "max_size": 512,
            "max_file_size": 5242880,
            "allowed_types": ["image/jpeg", "image/png", "image/gif", "image/webp"],
            "default_colors": [
                "#e94560",
                "#4ade80",
                "#fbbf24",
                "#60a5fa",
                "#a78bfa",
                "#f472b6",
            ],
        },
        "embeds": {
            "max_embeds_per_message": 10,
            "max_fields_per_embed": 25,
            "total_char_limit": 6000,
            "url_preview": {
                "enabled": True,
                "timeout_seconds": 8,
                "max_html_bytes": 524288,
                "max_redirects": 5,
                "max_image_size": 5242880,
                "cache_ttl_seconds": 3600,
                "rate_limit_requests": 10,
                "rate_limit_window_seconds": 60,
                "proxy_images": True,
                "allowed_schemes": ["http", "https"],
            },
        },
        "transcript_export": {
            "enabled": True,
            "max_messages_per_export": 10000,
            "max_date_range_days": 365,
            "allowed_formats": ["json", "csv", "txt", "html"],
            "rate_limit": {
                "requests_per_hour": 5,
                "requests_per_day": 20,
            },
            "max_file_size_mb": 50,
            "temporary_storage_hours": 24,
        },
        "telemetry": {
            "enabled": True,
            "rate_limit": {"max_per_minute": 10},
            "retention_days": 30,
        },
        "static_client": {
            "enabled": False,
            "serve": True,
            "install_dir": str(home_dir / "client"),
            "source": "gitlab_release",
            "version_pin": "match_server",
            "auto_update": False,
            "auto_update_min_age_seconds": 3600,
            "auto_update_check_interval_seconds": 3600,
            "git_lab": {
                "project_id": 2,
                "api_url": "https://gitlab.plexichat.com/api/v4",
                "private_token_env": "PLEXICHAT_GITLAB_TOKEN",
                "verify_tls": True,
                "request_timeout_seconds": 30,
            },
            "cache_control": {
                "hashed_assets": "public, max-age=31536000, immutable",
                "html": "no-store, max-age=0",
                "other": "public, max-age=300",
            },
            "security_headers": {
                "x_content_type_options": "nosniff",
                "x_frame_options": "SAMEORIGIN",
                "referrer_policy": "strict-origin-when-cross-origin",
                "permissions_policy": "geolocation=(), microphone=(self), camera=()",
                "content_security_policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-hashes' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; worker-src blob:; connect-src 'self' http://localhost:8000 http://127.0.0.1:8000 wss:; manifest-src 'self'; frame-ancestors 'none';",
            },
            "rate_limit": {
                "enabled": True,
                "html": {"requests": 300, "window_seconds": 60, "burst": 60},
                "assets": {"requests": 1200, "window_seconds": 60, "burst": 120},
            },
            "max_zip_size_bytes": 104857600,
            "spa_routes": {
                "/app": "app.html",
                "/settings": "settings.html",
                "/register": "register.html",
                "/forgot-password": "forgot-password.html",  # pragma: allowlist secret
                "/reset-password": "reset-password.html",  # pragma: allowlist secret
                "/oauth-callback": "oauth-callback.html",
                "/error": "error.html",
                "/invite": "app.html",
            },
            "log_downloads": False,
            "config_injection": {
                "enabled": True,
                "filename": "config.js",
                "public_server_url": "http://localhost:8000",
                "content": 'window.PLEXICHAT_CONFIG = { serverUrl: "{origin}", hideServerField: true, defaultTheme: "ocean", version: "{version}" };',
            },
            "invite_redirect": True,
        },
    }
