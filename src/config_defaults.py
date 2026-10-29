"""Default configuration for the Plexichat server.

Provides the baseline settings used when no configuration file is found.
"""

import secrets
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from .config_defaults_artifacts import get_artifacts_defaults
    from .config_defaults_authentication import get_authentication_defaults
except ImportError:  # pragma: no cover - direct/script import fallback
    from config_defaults_artifacts import get_artifacts_defaults
    from config_defaults_authentication import get_authentication_defaults

_artifacts_defaults = get_artifacts_defaults
_authentication_defaults = get_authentication_defaults


ARTIFACTS_LICENSE_FEATURES = {
    "artifacts": "Master artifacts feature (any artifact type).",
    "artifacts_whiteboard": "Licensed multi-user live whiteboard artifacts.",
    "voice_transcription": "Licensed automatic voice-call transcription.",
    "plexiscribe": "Licensed collaborative document editor (Plexiscribe).",
    "plexiscript": "Licensed collaborative code editor (Plexiscript).",
    "plexistash": "Licensed PlexiStash metadata vault.",
    "plexisum": "Licensed PlexiSum collaborative workbooks.",
}


def default_version() -> str:
    """Return the application version to stamp into a fresh configuration.

    ``src.server.lifecycle`` owns the ``VERSION`` constant that releases bump,
    so it is the single source of truth here. The import is deliberately lazy:
    this module is loaded during early bootstrap, and pulling the server package
    in at module scope would initialize it too early.

    Returns:
        The current application version string (e.g. "a.1.0-166").
    """
    from src.server.lifecycle import VERSION

    return VERSION


def get_default_config(version: Optional[str] = None) -> Dict[str, Any]:
    """Return the default configuration dictionary for Plexichat.

    Args:
        version: The current application version string. Defaults to the
            version the server itself runs, so generated configs never
            advertise a different build than the running process.

    Returns:
        A dictionary containing all default configuration keys and values.
    """
    if version is None:
        version = default_version()

    home_dir = Path.home() / ".plexichat"

    return {
        "application": {
            "name": "Plexichat",
            "version": version,
            "environment": "development",
        },
        "server": {"host": "127.0.0.1", "port": 8000, "reload": False},
        "logging": {
            "level": "DEBUG",
            "max_bytes": 10485760,
            "backup_count": 5,
            "zip_logs": True,
            "rotate": True,
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
                "cache_size_kb": -32000,
                "mmap_size": 268435456,
                "journal_size_limit": 67108864,
                "temp_store": "MEMORY",
                "idle_reclaim_seconds": 5.0,
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
        "valkey": {
            "enabled": False,
            "host": "${VALKEY_HOST:-localhost}",
            "port": 6379,
            "password": "${VALKEY_PASSWORD:-}",
            "db": 0,
            "ssl": False,
            "key_prefix": "plexichat:",
            "connection_pool": {
                "timeout": 5,
                "max_connections": 50,
            },
            "ttl": {"session": 1800, "presence": 300, "cache": 60},
            "cache_max_items": 1000,
        },
        "authentication": _authentication_defaults(home_dir),
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
            "max_worker_threads": 16,
        },
        "websocket": {
            "heartbeat_interval_ms": 45000,
            "session_timeout_ms": 60000,
            "max_connections_per_user": 5,
            "rate_limit_per_minute": 120,
            "max_message_size": 65536,
            "max_decompressed_size": 262144,
            "compression_enabled": True,
            "allowed_origins": [],
        },
        "messaging": {
            "encrypt_messages": True,
            "encrypt_attachments": True,
            "max_message_length": 4000,
            "max_group_participants": 100,
            "max_attachment_size": 10485760,
            "max_attachments_per_message": 10,
            "dm_auto_create": True,
            "message_preview_length": 100,
        },
        "media": {
            "data_dir": str(home_dir / "data"),
            "logs_dir": str(home_dir / "logs"),
            "media_dir": str(home_dir / "media"),
            "temp_dir": str(home_dir / "temp"),
            "storage_backend": "local",
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
            "database_max_size": 524288,
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
                "max_total_size_per_day": 536870912,
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
            "sfu_backend": "aiortc",
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
            "acoustic_defense": {
                "enabled": False,
                "jitter_ms_min": 5.0,
                "jitter_ms_max": 20.0,
                "spectral_masking": True,
                "spectral_mask_noise_db": -40.0,
                "spectral_mask_low_hz": 1000.0,
                "spectral_mask_high_hz": 8000.0,
                "vad_gating": True,
                "vad_speech_threshold": 0.02,
                "vad_silence_frames": 3,
                "transient_shaving": True,
                "transient_attack_ms": 0.5,
                "transient_release_ms": 5.0,
                "transient_ratio": 0.3,
            },
        },
        "search": {
            "enabled": True,
            "backend": "sqlite_fts5",
            "result_limit": 100,
            "batch_size": 100,
            "write_time_indexing": True,
            "reindex_page_size": 200,
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
            "state_cookie_required": True,
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
            "key_rotation_days": 180,
            "password_pepper": {
                # Server-side pepper: HMAC-SHA256(pepper, password) before
                # Argon2id. The pepper secret lives in the system keyring
                # (KEK-encrypted at rest), so there is nothing for the
                # operator to provision: it is generated once via CSRNG on
                # first use. Keep old ids listed for verification after a
                # rotation; `current_id` is used for new hashes. Logins with
                # a stale-pepper hash rehash transparently on success.
                # Each entry: {"source": "keyring"|"hsm"|"env"|"file"|
                #   "kek-derived", ...}. `env`/`file` exist only for
                #   migration from externally-managed secrets.
                "enabled": True,
                "current_id": "v1",
                "peppers": {"v1": {"source": "keyring"}},
            },
            "hsm": {
                "enabled": False,
                "library_path": "/usr/lib/softhsm/libsofthsm2.so",
                "slot_id": 0,
                "token_label": "",
                # PIN hygiene: prefer `pin_env` (env var name) or `pin_file`
                # (path to a file containing the PIN). A literal `pin`
                # supports ${VAR} expansion but warns — never commit it.
                "pin": "",
                "pin_env": "PLEXICHAT_HSM_PIN",
                "pin_file": "",
                "key_label": "plexichat_kek",
                # KEK extraction policy. The legacy vault path returns the
                # raw KEK bytes (software AES-GCM), which requires an
                # extractable key. Set to False once callers migrate to
                # hsm_encrypt()/hsm_decrypt() below — the vault then refuses
                # to export CKA_VALUE and only does crypto inside the token.
                "key_extractable": True,
                "allow_generate": True,
            },
            "tpm": {
                "enabled": True,
                "hierarchy": "owner",
                "key_template": "aes256",
                # KDF label/salt version the TPM-bound KEK. Bump `label`
                # to rotate TPM-derived KEKs (old keyrings then need the
                # previous label via kek_migration).
                "label": "PLEXICHAT_KEK_V2",
                "salt": "plexichat:tpm-kek:v2",
                # Optional persistent handle (e.g. 0x81000001). When set,
                # the primary is persisted with EvictControl so later boots
                # skip create_primary. Empty means transient (default).
                "persistent_handle": "",
                # Optional PCR selection for sealing (e.g. [0, 1, 7]).
                # Empty means no PCR policy (portable across updates).
                "pcr_selection": [],
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
            "enable_admin_tests": True,
            "journal_path": str(home_dir / "data" / "selftest_artifact_journal.json"),
            "retry_non_idempotent": False,
            "excluded_endpoints": ["/api/v1/auth/logout", "/api/v1/admin/logout"],
            "test_user": {
                "username": "selftest_admin",
                "email": "selftest@plexichat.com",
                "password": None,
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
            "hostname": "localhost",
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
            "max_packs_per_user": 100,
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
            "max_webhooks_per_channel": 10,
            "max_webhooks_per_server": 50,
            "max_message_length": 2000,
            "max_embeds_per_message": 10,
            "rate_limit_per_minute": 30,
            "delivery_interval_seconds": 30,
        },
        "automod": {
            "enabled": True,
            "exempt_owners": True,
            "exempt_admins": True,
            "fail_closed": True,
            "default_alert_channel_id": None,
            "rules": {
                "caps": {
                    "enabled": True,
                    "max_percentage": 70.0,
                    "min_length": 10,
                    "ignore_commands": True,
                }
            },
            "ai": {
                "shieldstral": {
                    "endpoint_url": "",
                    "api_key": "",
                    "model": "shieldstral",
                    "threshold": 0.5,
                    "policy_query": (
                        "Evaluate the following content for harmful, toxic, "
                        "hateful, sexually explicit, or unsafe material. Answer "
                        "with a single word only: 'yes' if it is harmful, 'no' "
                        "if it is not."
                    ),
                    "timeout_seconds": 60,
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
        "artifacts": _artifacts_defaults(home_dir),
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
            # M3: decompression-bomb guards consumed by the avatars image
            # pipeline (manager/processing.py). These keys were previously
            # absent here, so operator overrides were silently ignored and
            # the hardcoded fallbacks always won. Values mirror
            # media.image_processing.
            "max_dimension": 16384,
            "max_pixels": 178956970,
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
            "enabled": False,
            "rate_limit": {"max_per_minute": 10},
            "retention_days": 30,
            "max_distinct_endpoints": 500,
            "max_total_rows": 100000,
            "percentile_sample_rows": 25000,
            "client_hash_salt": secrets.token_hex(16),
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
                "content_security_policy": "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; media-src 'self' blob: data:; worker-src blob:; connect-src 'self' http://localhost:8000 http://127.0.0.1:8000 wss:; manifest-src 'self'; frame-ancestors 'none';",
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
                "public_server_url": "",
                "content": 'window.PLEXICHAT_CONFIG = { serverUrl: "{origin}", hideServerField: true, defaultTheme: "ocean", version: "{version}" };',
            },
            "invite_redirect": True,
        },
    }
