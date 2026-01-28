#!/usr/bin/env python3
"""
PlexiChat Server - Main Entry Point

This script initializes all core modules and starts the API server.
Supports clean shutdown (Ctrl+C) with client notification and restart capability.
"""

import os
import sys
import signal
import asyncio
import threading
import argparse
import secrets
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

# Setup paths
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

# Import utilities
import utils.logger as logger  # noqa: E402
import utils.config as config  # noqa: E402
import utils.validator as validator  # noqa: E402
import utils.version as version  # noqa: E402

# Global Version Definition
VERSION = "a.1.0-34"


class PlexiChatServer:
    """Main server class with lifecycle management."""

    def __init__(self):
        self.app = None
        self.server = None
        self.db = None
        self.shutdown_event = threading.Event()
        self.restart_requested = False
        self._modules = {}

    def get_default_config(self) -> Dict[str, Any]:
        """Get default configuration with home folder data storage."""
        home_dir = Path.home() / ".plexichat"

        return {
            "logging": {
                "level": "DEBUG",
                "max_bytes": 10485760,
                "backup_count": 5,
                "zip_logs": True,
                "rotate": True,
                # SECURITY WARNING: Set to False in production to avoid leaking sensitive info
                "include_exception_details": False,
            },
            "selftest": {
                "enabled": False,
                "run_on_startup": False,
                "exit_on_failure": False,
                "capture_stack_traces": True,
                "retry_on_failure": True,
                "excluded_endpoints": ["/api/v1/auth/logout", "/api/v1/admin/logout"],
                "test_user": {
                    "username": "selftest_admin",
                    "email": "selftest@internal.local",
                    "password": "SelfTest_Password_123!",
                },
            },
            "database": {
                "type": "sqlite",
                "path": str(home_dir / "data" / "plexichat.db"),
                "postgres": {
                    "host": "localhost",
                    "port": 5432,
                    "user": "postgres",
                    "password": "",
                    "dbname": "plexichat",
                    "sslmode": "prefer",
                },
                "connection_pool": {
                    "min_connections": 1,
                    "max_connections": 10,
                    "connect_timeout": 10,
                    "max_idle_time": 300,
                    "validation_interval": 60,
                    "enable_validation": True,
                    "validation_query": "SELECT 1",
                },
            },
            "authentication": {
                "accounts": {
                    "allow_registration": True,
                    "require_email_verification": False,
                    "max_bots_per_user": 5,
                    "username_min_length": 3,
                    "username_max_length": 32,
                    "age_gate_enabled": False,
                    "minimum_age": 13,
                    # "boolean" (store only verified bit) or "dob" (store date of birth)
                    "age_verification_type": "boolean",
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
                    "issuer": "PlexiChat",
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
                },
                "bots": {"token_bytes": 48, "require_owner_2fa": False},
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
                }
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
                }
            },
            # OAuth configuration for external identity providers
            # SECURITY: Store client_secret values in environment variables for production:
            # - OAUTH_GOOGLE_CLIENT_SECRET
            # - OAUTH_GITHUB_CLIENT_SECRET
            # - OAUTH_MICROSOFT_CLIENT_SECRET
            "oauth": {
                # State token TTL in seconds (default: 600 = 10 minutes)
                # Shorter is more secure but may cause issues with slow connections
                "state_ttl_seconds": 600,
                # Entropy for state tokens in bytes (minimum 32 recommended)
                "state_token_bytes": 32,
                # Entropy for OIDC nonce in bytes (minimum 32 recommended)
                "nonce_token_bytes": 32,
                # Clean up expired states on each verification (recommended: true)
                "cleanup_on_verify": True,
                # Maximum pending OAuth states per IP address (0 = unlimited)
                # Helps prevent state flooding attacks
                "max_states_per_ip": 10,
                # Enable PKCE (Proof Key for Code Exchange) for supported providers
                # Recommended: True for better security against code interception
                "pkce_enabled": True,
                # PKCE configuration
                "pkce": {
                    # Length of random bytes for code verifier (32-96, default 64)
                    "verifier_length": 64,
                    # Minimum verifier length per RFC 7636 (do not change unless required)
                    "min_verifier_length": 43,
                    # Maximum verifier length per RFC 7636 (do not change unless required)
                    "max_verifier_length": 128,
                },
                # Google OAuth configuration
                "google": {"client_id": "", "client_secret": ""},
                # GitHub OAuth configuration
                "github": {"client_id": "", "client_secret": ""},
                # Microsoft OAuth configuration (works with personal and work accounts)
                "microsoft": {"client_id": "", "client_secret": ""},
            },
            "encryption": {
                "argon2": {
                    "time_cost": 2,
                    "memory_cost": 65536,
                    "parallelism": 2,
                    "hash_length": 32,
                    "salt_length": 16,
                },
                "aes_gcm": {"key_length": 32, "nonce_length": 12, "tag_length": 16},
                "snowflake": {
                    "epoch": "2024-01-01T00:00:00Z",
                    # Worker/datacenter IDs are auto-derived from environment or machine characteristics
                    # For distributed deployments, set PLEXICHAT_WORKER_ID and PLEXICHAT_DATACENTER_ID
                    # environment variables to ensure unique IDs across instances (0-31 each)
                    "worker_id": None,  # Auto-derived if not set
                    "datacenter_id": None,  # Auto-derived if not set
                },
                "key_rotation_days": 90,
            },
            # Message encryption at rest - encrypts message content in database
            # Key is auto-generated and stored in ~/.plexichat/data/.message_encryption_key
            # IMPORTANT: Back up this key file! Without it, encrypted messages cannot be recovered.
            "messaging": {
                "encrypt_messages": True,  # Enable message encryption at rest (default: True)
                "encrypt_attachments": True,  # Encrypt attachment URLs in DB (replaces url with '[encrypted]')
                "max_message_length": 4000,
                "max_group_participants": 100,
                "max_attachment_size": 10485760,  # 10MB
                "max_attachments_per_message": 10,
                "dm_auto_create": True,
                "message_preview_length": 100,
            },
            "presence": {
                "typing_timeout_ms": 10000,
                "timeout_ms": 300000,
                "update_interval_ms": 60000,
            },
            "stickers": {
                "max_packs_per_server": 50,
                "max_stickers_per_pack": 100,
                "max_sticker_size": 524288,  # 512KB
                "max_sticker_name_length": 32,
                "max_pack_name_length": 64,
                "max_pack_description_length": 256,
                "allowed_formats": ["image/png", "image/webp", "image/gif"],
                "max_suggestions": 10,
            },
            "soundboard": {
                "max_sounds_per_server": 50,
                "max_sound_size": 2097152,  # 2MB
                "max_sound_duration_seconds": 10,
                "max_sound_name_length": 64,
                "allowed_formats": [
                    "audio/mpeg",
                    "audio/ogg",
                    "audio/wav",
                    "audio/webm",
                ],
                "default_cooldown_seconds": 3.0,
                "max_cooldown_seconds": 30.0,
            },
            "webhooks": {
                "max_webhooks_per_channel": 10,
                "max_webhooks_per_server": 50,
                "max_message_length": 2000,
                "max_embeds_per_message": 10,
            },
            "automod": {
                "enabled": True,
                "rules": {
                    "caps": {
                        "enabled": True,
                        "max_percentage": 70.0,
                        "min_length": 10,
                        "ignore_commands": True,
                    }
                },
            },
            "redis": {
                "enabled": False,
                "host": "localhost",
                "port": 6379,
                "password": "",
                "db": 0,
                "ssl": False,
                "key_prefix": "plexichat:",
                "connection_pool": {"max_connections": 50, "timeout": 5},
                "ttl": {"session": 1800, "presence": 300, "cache": 60},
                "cache_max_items": 1000,
            },
            "api": {
                "title": "Plexichat API",
                "description": "REST API for Plexichat messaging platform",
                "version": VERSION,
                "api_prefix": "/api/v1",
                "debug": True,
                # SECURITY: In development, allow localhost origins.
                # For production, change to specific allowed origins like ["https://yourdomain.com"]
                "cors_origins": [
                    "http://localhost:5000",
                    "http://127.0.0.1:5000",
                    "http://localhost:8000",
                    "http://127.0.0.1:8000",
                    "https://plexichat-app.tail79f345.ts.net:8443",
                    "https://plexichat-app.tail79f345.ts.net",
                    "http://localhost:8443",
                    "https://linuxofuser.pythonanywhere.com",
                    "http://linuxofuser.pythonanywhere.com",
                ],
                "cors_allow_credentials": True,
                "cors_allow_methods": [
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "OPTIONS",
                ],
                "cors_allow_headers": [
                    "Authorization",
                    "Content-Type",
                    "X-Requested-With",
                    "Accept",
                    "Origin",
                ],
                # Trusted proxies for IP extraction (empty = none)
                "trusted_proxies": [],
                # Whether to trust X-Forwarded-For (requires trusted_proxies to be set for security)
                "trust_x_forwarded_for": False,
                "docs_url": "/docs",
                "redoc_url": "/redoc",
                "openapi_url": "/openapi.json",
            },
            "server": {"host": "0.0.0.0", "port": 8000, "workers": 1, "reload": False},
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
                }
            },
            "websocket": {
                "heartbeat_interval_ms": 45000,
                "session_timeout_ms": 60000,
                "max_connections_per_user": 5,
                "rate_limit_per_minute": 120,
                # Compression security settings
                "max_message_size": 65536,  # 64KB max message size
                "max_decompressed_size": 262144,  # 256KB max decompressed size (prevents zip bombs)
                "compression_enabled": True,
                # Origin validation (empty = allow all, for dev; set specific origins in production)
                "allowed_origins": [],
            },
            "application": {
                "name": "Plexichat",
                "version": VERSION,
                "environment": "development",
            },
            "versioning": {"min_supported_version": VERSION, "update_url": None},
            "rate_limiting": {
                "enabled": True,
                # Global rate limit (per user, across all requests)
                "global": {"requests": 50, "window_seconds": 1.0, "burst": 10},
                # Per-user general limit
                "user": {
                    "requests": 120,
                    "window_seconds": 60.0,
                    "burst": 20,
                    "hourly_limit": 3600,
                    "daily_limit": 50000,
                },
                # Per-IP limit (for unauthenticated requests)
                "ip": {
                    "requests": 60,
                    "window_seconds": 60.0,
                    "burst": 10,
                    "hourly_limit": 1800,
                    "daily_limit": 10000,
                },
                # Bot multiplier (bots get this multiplier on limits)
                "bot_multiplier": 1.5,
                # Webhook multiplier
                "webhook_multiplier": 1.0,
                # Bypass for admins/internal requests
                "admin_bypass": True,
                "internal_bypass": True,
                "bypass_secret": secrets.token_hex(32),
            },
            "docs": {
                "enabled": True,
                "path": "/docs/api",
                "title": "PlexiChat API Documentation",
                "description": "Complete API documentation for PlexiChat messaging platform",
                "base_url": "http://localhost:8000",
                "websocket_url": "ws://localhost:8000/gateway",
                "theme": {
                    "style": "dark",
                    "primary_color": "#e94560",
                    "background_color": "#1a1a2e",
                    "text_color": "#eaeaea",
                    "code_background": "#16213e",
                    "border_color": "#0f3460",
                },
                "rate_limit": {"enabled": True, "requests": 60, "window_seconds": 60},
                "cache": {"enabled": True, "ttl_seconds": 300},
                "security": {"require_auth": False},
            },
            "feedback": {
                "enabled": True,
                "rate_limit": {"max_per_hour": 5, "max_per_day": 20},
            },
            "email": {
                "smtp_host": "localhost",
                "smtp_port": 587,
                "smtp_user": "",
                "from_email": "noreply@plexichat.internal",
                "use_tls": True,
            },
            "media": {
                # Directories
                "data_dir": str(home_dir / "data"),
                "logs_dir": str(home_dir / "logs"),
                "media_dir": str(home_dir / "media"),
                "temp_dir": str(home_dir / "temp"),
                # Primary storage backend: "local", "s3", or "database"
                "storage_backend": "local",
                # Client-side encryption at rest
                # When enabled, files are encrypted with AES-256-GCM before storage
                # Keys are stored in ~/.plexichat/data/file_keyring.json
                # IMPORTANT: Back up this keyring file! Without it, encrypted files cannot be recovered.
                "encrypt_at_rest": True,
                # Local filesystem storage
                "local_path": str(home_dir / "media"),
                "local_url": "/media",
                # S3/MinIO storage (used when storage_backend is "s3")
                "s3_bucket": "",
                "s3_access_key": "",
                "s3_secret_key": "",
                "s3_region": "us-east-1",
                "s3_endpoint": "",  # Custom endpoint for MinIO
                "s3_public_url": "",
                # Database BLOB storage (used when storage_backend is "database")
                "database_url": "/api/v1/media/blob",
                "database_max_size": 524288,  # 512KB max for DB storage
                # Auto-routing: route small files to database regardless of primary backend
                "auto_route_to_database": {
                    "enabled": True,
                    "max_size": 524288,  # Files under 512KB
                    "content_types": [  # Only these types get routed to DB
                        "text/plain",
                        "application/json",
                        "text/markdown",
                        "text/csv",
                    ],
                },
                # File size limits per media type (in bytes)
                "size_limits": {
                    "image": 10485760,  # 10MB
                    "video": 104857600,  # 100MB
                    "audio": 52428800,  # 50MB
                    "document": 26214400,  # 25MB
                    "icon": 2097152,  # 2MB (server/user icons)
                    "avatar": 5242880,  # 5MB (user avatars)
                    "other": 10485760,  # 10MB
                },
                # Allowed content types per media type
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
                # Thumbnail generation
                "thumbnail_sizes": [64, 128, 256, 512],
                "image_quality": 85,
                "image_optimize": True,
                # Image processing security limits (DoS protection)
                "image_processing": {
                    "max_dimension": 16384,  # Max width/height in pixels
                    "max_pixels": 178956970,  # Max total pixels (~13400x13400)
                    "max_thumbnail_requests_per_minute": 60,  # Rate limit for thumbnail generation
                },
                # Video processing security limits
                "video_processing": {
                    "ffprobe_timeout": 30,  # Timeout in seconds for ffprobe
                    "max_size_for_metadata": 524288000,  # 500MB max for metadata extraction
                },
                # URL signing for secure access
                "signing_key": "CHANGE_THIS_SIGNING_KEY",
                "signing_expiry": 3600,  # 1 hour
                # Malware scanning (ClamAV)
                "scanner_enabled": False,
                "scanner_host": "localhost",
                "scanner_port": 3310,
                # External URL proxy (SSRF protected, but consider disabling for maximum production privacy)
                "proxy_enabled": True,
                "proxy_cache_ttl": 86400,  # 24 hours
                "proxy_max_size": 10485760,  # 10MB
                "proxy_buffer_size": 65536,  # 64KB buffer for streaming
                # Rate limiting for uploads
                "rate_limit": {
                    "enabled": True,
                    "uploads_per_minute": 10,
                    "uploads_per_hour": 100,
                    "max_total_size_per_day": 536870912,  # 512MB per user per day
                },
                # Perceptual hashing for image similarity detection
                # Requires: pip install imagehash Pillow
                "phash": {
                    "enabled": True,
                    # Hash algorithm: phash (DCT), dhash (difference), ahash (average), whash (wavelet)
                    "algorithm": "phash",
                    # Hash size (8 = 64-bit hash, higher = more precise but slower)
                    "hash_size": 8,
                    # Similarity threshold (Hamming distance, lower = stricter matching)
                    # 0 = exact match only, 10 = allows minor modifications, 20 = very lenient
                    "similarity_threshold": 10,
                    # High frequency factor for pHash DCT (default 4)
                    "highfreq_factor": 4,
                },
                # Deduplication settings
                "deduplication": {
                    "enabled": True,
                    "hash_algorithm": "sha256",
                    "min_size": 10240,  # 10KB minimum for deduplication
                    "auto_block_threshold": 5,  # Auto-block after N reports
                },
            },
            # Avatars configuration (user avatars and server icons stored in database)
            "avatars": {
                # Maximum dimension for avatars (width and height)
                "max_size": 512,
                # Maximum file size before processing (5MB)
                "max_file_size": 5242880,
                # Allowed content types
                "allowed_types": ["image/jpeg", "image/png", "image/gif", "image/webp"],
            },
            # Embeds configuration (rich embeds and URL previews)
            "embeds": {
                "max_embeds_per_message": 10,
                "max_fields_per_embed": 25,
                "total_char_limit": 6000,
                # URL preview settings (link unfurling)
                "url_preview": {
                    "enabled": True,
                    # Request timeout for fetching URLs
                    "timeout_seconds": 8,
                    # Maximum HTML size to download
                    "max_html_bytes": 524288,  # 512KB
                    # Maximum redirects to follow
                    "max_redirects": 5,
                    # Maximum image size for preview thumbnails
                    "max_image_size": 5242880,  # 5MB
                    # Cache TTL for preview metadata
                    "cache_ttl_seconds": 3600,  # 1 hour
                    # Rate limiting per user
                    "rate_limit_requests": 10,
                    "rate_limit_window_seconds": 60,
                    # Proxy images through our server (prevents IP leakage)
                    "proxy_images": True,
                    # Allowed URL schemes
                    "allowed_schemes": ["http", "https"],
                },
            },
            # Telemetry configuration
            "telemetry": {
                "enabled": True,
                "rate_limit": {"max_per_minute": 10},
                "retention_days": 30,
            },
            # Voice/Video configuration
            "voice": {
                "enabled": True,
                "sfu_backend": "mediasoup",
                "mediasoup_url": "https://localhost:4443",
                "janus_url": "http://localhost:8088/janus",
                # STUN servers for NAT traversal (free Google servers)
                "stun_urls": [
                    "stun:stun.l.google.com:19302",
                    "stun:stun1.l.google.com:19302",
                    "stun:stun2.l.google.com:19302",
                    "stun:stun3.l.google.com:19302",
                ],
                # TURN servers for relay (required for restrictive NATs/firewalls)
                # Using metered.ca free TURN servers as fallback - replace with your own coturn for production
                "turn_urls": [],
                "turn_username": "",
                "turn_credential": "",
                # TURN secret for time-limited credentials (coturn static-auth-secret)
                "turn_secret": "",
                "turn_ttl": 86400,
                "log_connections": False,
            },
            # Admin UI configuration
            # SECURITY WARNING: The admin panel provides access to sensitive user data
            # including feedback, telemetry, and system statistics.
            # NEVER disable host_restriction in production without other security measures!
            "admin_ui": {
                "enabled": True,
                "path": "/admin",
                # Set to False to disable 2FA requirement for admin login
                # WARNING: Disabling OTP reduces security significantly!
                "require_otp": True,
                "host_restriction": {
                    # WARNING: Disabling this allows ANYONE to access the admin panel!
                    # Only disable if you have VPN, firewall, or reverse proxy auth in place.
                    "enabled": True,
                    "allowed_hosts": ["127.0.0.1", "localhost", "::1"],
                },
                # List of IP addresses or prefixes to explicitly block
                "blocked_ips": [],
                # Allowed origins for admin panel CORS (empty = use main api.cors_origins)
                "allowed_origins": [],
                # Rate limiting for admin login attempts
                "rate_limit": {
                    "max_attempts": 5,
                    "window_seconds": 300,
                    "lockout_seconds": 900,
                },
            },
            # TLS/HTTPS configuration
            "tls": {
                "enabled": False,
                "auto_generate_self_signed": False,
                "cert_path": str(home_dir / "certs" / "server.crt"),
                "key_path": str(home_dir / "certs" / "server.key"),
                "cert_days": 365,
            },
            # User Features configuration
            # Controls feature flags, badges, and rate limit tiers for users
            # Tiers can be customized with specific limits for voice, video, uploads, etc.
            "user_features": {
                # Alpha registration mode - when enabled, new users automatically get:
                # - The 'alpha' tier (higher limits)
                # - The 'alpha_tester' badge
                # Set to true during alpha testing, false to disable for new registrations
                "alpha_registration_enabled": False,
                # Default tier for new users (when alpha_registration_enabled is false)
                "default_tier": "standard",
                # Maximum badges displayed on profile
                "badge_display_limit": 5,
                # Available badges (admin can assign these to users)
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
                # Rate limit tiers - define custom tiers with specific limits
                # Each tier has a multiplier for general rate limits plus specific feature limits
                # -1 means unlimited for that feature
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
                # Rate limits for feature management endpoints (admin only)
                "admin_rate_limit": {"max_per_minute": 30, "max_per_hour": 200},
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
        }

    def setup_directories(self) -> None:
        """Create necessary directories in home folder."""
        home_dir = Path.home() / ".plexichat"
        dirs = ["data", "logs", "media", "temp", "config"]

        for d in dirs:
            dir_path = home_dir / d
            dir_path.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Created directories in {home_dir}")

    def setup_config(self) -> str:
        """Setup configuration from file or defaults, with environment variable overrides.
        
        Config file search order:
        1. Project config: ./config/config.yaml
        2. Home folder config: ~/.plexichat/config/config.yaml
        """
        # Try project config first, then home folder config
        config_paths = [
            (os.path.join(project_root, "config", "config.yaml"), "project directory"),
            (str(Path.home() / ".plexichat" / "config" / "config.yaml"), "home directory"),
        ]

        config_path = None
        for cp, source in config_paths:
            if os.path.exists(cp):
                config_path = str(cp)
                break

        if not config_path:
            # Use project config as default (will be created)
            config_path = str(config_paths[0][0])

        config.setup(config_path=config_path, default_config=self.get_default_config())

        # Apply environment variable overrides
        self._apply_env_overrides()

        return config_path

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides to configuration.
        
        Supports the following environment variables:
        
        DATABASE CONFIGURATION:
        - DATABASE_URL: Full PostgreSQL or SQLite connection string
        - POSTGRES_HOST: PostgreSQL host (default: localhost)
        - POSTGRES_PORT: PostgreSQL port (default: 5432)
        - POSTGRES_USER: PostgreSQL username (default: postgres)
        - POSTGRES_PASSWORD: PostgreSQL password (required for production)
        - POSTGRES_DBNAME: PostgreSQL database name (default: plexichat)
        - POSTGRES_SSLMODE: PostgreSQL SSL mode (default: prefer)
        
        DATABASE CONNECTION POOL:
        - DB_POOL_MIN_CONNECTIONS: Minimum connections (default: 1)
        - DB_POOL_MAX_CONNECTIONS: Maximum connections (default: 10)
        - DB_POOL_CONNECT_TIMEOUT: Connection timeout in seconds (default: 10)
        - DB_POOL_MAX_IDLE_TIME: Idle timeout in seconds (default: 300)
        - DB_POOL_VALIDATION_INTERVAL: Validation check interval in seconds (default: 60)
        - DB_POOL_ENABLE_VALIDATION: Enable connection validation (default: true)
        - DB_POOL_VALIDATION_QUERY: Query for validation (default: SELECT 1)
        
        MONITORING:
        - MONITORING_ENABLED: Enable monitoring (default: true)
        - MONITORING_LOG_INTERVAL: Metrics log interval in seconds (default: 300)
        - MONITORING_METRICS_ENABLED: Enable metrics collection (default: true)
        - MONITORING_ALERT_CPU_THRESHOLD: CPU alert threshold % (default: 80)
        - MONITORING_ALERT_MEMORY_THRESHOLD: Memory alert threshold % (default: 85)
        - MONITORING_ALERT_DB_POOL_THRESHOLD: DB pool saturation % (default: 75)
        - MONITORING_ALERT_QUERY_TIME_MS: Query timeout alert in ms (default: 5000)
        - MONITORING_ALERT_DB_ERRORS_PER_MINUTE: DB error rate alert (default: 10)
        - MONITORING_ALERT_API_RESPONSE_TIME_MS: API response time alert (default: 2000)
        - MONITORING_ALERT_ERROR_RATE_PERCENT: Error rate alert % (default: 5)
        - MONITORING_ALERT_ACTIVE_CONNECTIONS: Connection count alert (default: 1000)
        
        LOGGING:
        - LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        import urllib.parse
        
        db_config = config.get("database", {})
        
        # Handle DATABASE_URL (takes precedence over individual env vars)
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            if database_url.startswith("postgres://") or database_url.startswith(
                "postgresql://"
            ):
                # Parse PostgreSQL URL
                parsed = urllib.parse.urlparse(database_url)
                db_config["type"] = "postgres"
                db_config["postgres"] = {
                    "host": parsed.hostname or "localhost",
                    "port": parsed.port or 5432,
                    "user": parsed.username or "postgres",
                    "password": parsed.password or "",
                    "dbname": parsed.path.lstrip("/") if parsed.path else "plexichat",
                    "sslmode": "prefer",
                }
                # Parse query params for sslmode
                if parsed.query:
                    params = urllib.parse.parse_qs(parsed.query)
                    if "sslmode" in params:
                        db_config["postgres"]["sslmode"] = params["sslmode"][0]
            elif database_url.startswith("sqlite:///"):
                db_config["type"] = "sqlite"
                db_config["path"] = database_url[10:]  # Remove sqlite:///
        else:
            # Handle individual PostgreSQL environment variables
            if not db_config.get("postgres"):
                db_config["postgres"] = {}
            
            postgres_config = db_config["postgres"]
            
            # PostgreSQL connection credentials
            postgres_host = os.getenv("POSTGRES_HOST")
            if postgres_host:
                postgres_config["host"] = postgres_host
            
            postgres_port = os.getenv("POSTGRES_PORT")
            if postgres_port:
                try:
                    postgres_config["port"] = int(postgres_port)
                except ValueError:
                    logger.warning(f"Invalid POSTGRES_PORT value: {postgres_port}, using default")
            
            postgres_user = os.getenv("POSTGRES_USER")
            if postgres_user:
                postgres_config["user"] = postgres_user
            
            postgres_password = os.getenv("POSTGRES_PASSWORD")
            if postgres_password:
                postgres_config["password"] = postgres_password
            
            postgres_dbname = os.getenv("POSTGRES_DBNAME")
            if postgres_dbname:
                postgres_config["dbname"] = postgres_dbname
            
            postgres_sslmode = os.getenv("POSTGRES_SSLMODE")
            if postgres_sslmode:
                postgres_config["sslmode"] = postgres_sslmode
        
        # Initialize connection pool config if not present
        if "connection_pool" not in db_config:
            db_config["connection_pool"] = {}
        
        pool_config = db_config["connection_pool"]
        
        # Connection pool environment variables
        db_pool_min = os.getenv("DB_POOL_MIN_CONNECTIONS")
        if db_pool_min:
            try:
                pool_config["min_connections"] = int(db_pool_min)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_MIN_CONNECTIONS value: {db_pool_min}")
        
        db_pool_max = os.getenv("DB_POOL_MAX_CONNECTIONS")
        if db_pool_max:
            try:
                pool_config["max_connections"] = int(db_pool_max)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_MAX_CONNECTIONS value: {db_pool_max}")
        
        db_pool_timeout = os.getenv("DB_POOL_CONNECT_TIMEOUT")
        if db_pool_timeout:
            try:
                pool_config["connect_timeout"] = int(db_pool_timeout)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_CONNECT_TIMEOUT value: {db_pool_timeout}")
        
        db_pool_idle = os.getenv("DB_POOL_MAX_IDLE_TIME")
        if db_pool_idle:
            try:
                pool_config["max_idle_time"] = int(db_pool_idle)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_MAX_IDLE_TIME value: {db_pool_idle}")
        
        db_pool_validation_interval = os.getenv("DB_POOL_VALIDATION_INTERVAL")
        if db_pool_validation_interval:
            try:
                pool_config["validation_interval"] = int(db_pool_validation_interval)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_VALIDATION_INTERVAL value: {db_pool_validation_interval}")
        
        db_pool_enable_validation = os.getenv("DB_POOL_ENABLE_VALIDATION")
        if db_pool_enable_validation:
            pool_config["enable_validation"] = db_pool_enable_validation.lower() in ("true", "1", "yes")
        
        db_pool_validation_query = os.getenv("DB_POOL_VALIDATION_QUERY")
        if db_pool_validation_query:
            pool_config["validation_query"] = db_pool_validation_query
        
        config.set("database", db_config)
        
        # Monitoring configuration environment variables
        monitoring_config = config.get("monitoring", {})
        
        monitoring_enabled = os.getenv("MONITORING_ENABLED")
        if monitoring_enabled:
            monitoring_config["enabled"] = monitoring_enabled.lower() in ("true", "1", "yes")
        
        monitoring_log_interval = os.getenv("MONITORING_LOG_INTERVAL")
        if monitoring_log_interval:
            try:
                monitoring_config["log_interval"] = int(monitoring_log_interval)
            except ValueError:
                logger.warning(f"Invalid MONITORING_LOG_INTERVAL value: {monitoring_log_interval}")
        
        monitoring_metrics = os.getenv("MONITORING_METRICS_ENABLED")
        if monitoring_metrics:
            monitoring_config["metrics_enabled"] = monitoring_metrics.lower() in ("true", "1", "yes")
        
        # Initialize alert thresholds if not present
        if "alert_thresholds" not in monitoring_config:
            monitoring_config["alert_thresholds"] = {}
        
        alert_thresholds = monitoring_config["alert_thresholds"]
        
        # Individual alert threshold environment variables
        cpu_threshold = os.getenv("MONITORING_ALERT_CPU_THRESHOLD")
        if cpu_threshold:
            try:
                alert_thresholds["cpu_percent"] = float(cpu_threshold)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_CPU_THRESHOLD value: {cpu_threshold}")
        
        memory_threshold = os.getenv("MONITORING_ALERT_MEMORY_THRESHOLD")
        if memory_threshold:
            try:
                alert_thresholds["memory_percent"] = float(memory_threshold)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_MEMORY_THRESHOLD value: {memory_threshold}")
        
        db_pool_threshold = os.getenv("MONITORING_ALERT_DB_POOL_THRESHOLD")
        if db_pool_threshold:
            try:
                alert_thresholds["db_pool_saturation_percent"] = float(db_pool_threshold)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_DB_POOL_THRESHOLD value: {db_pool_threshold}")
        
        query_time = os.getenv("MONITORING_ALERT_QUERY_TIME_MS")
        if query_time:
            try:
                alert_thresholds["query_time_ms"] = int(query_time)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_QUERY_TIME_MS value: {query_time}")
        
        db_errors = os.getenv("MONITORING_ALERT_DB_ERRORS_PER_MINUTE")
        if db_errors:
            try:
                alert_thresholds["db_errors_per_minute"] = int(db_errors)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_DB_ERRORS_PER_MINUTE value: {db_errors}")
        
        api_response_time = os.getenv("MONITORING_ALERT_API_RESPONSE_TIME_MS")
        if api_response_time:
            try:
                alert_thresholds["api_response_time_ms"] = int(api_response_time)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_API_RESPONSE_TIME_MS value: {api_response_time}")
        
        error_rate = os.getenv("MONITORING_ALERT_ERROR_RATE_PERCENT")
        if error_rate:
            try:
                alert_thresholds["error_rate_percent"] = float(error_rate)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_ERROR_RATE_PERCENT value: {error_rate}")
        
        active_connections = os.getenv("MONITORING_ALERT_ACTIVE_CONNECTIONS")
        if active_connections:
            try:
                alert_thresholds["active_connections"] = int(active_connections)
            except ValueError:
                logger.warning(f"Invalid MONITORING_ALERT_ACTIVE_CONNECTIONS value: {active_connections}")
        
        config.set("monitoring", monitoring_config)
        
        # LOG_LEVEL override
        log_level = os.getenv("LOG_LEVEL")
        if log_level:
            log_config = config.get("logging", {})
            log_config["level"] = log_level.upper()
            config.set("logging", log_config)

    def validate_config(self) -> None:
        """Validate current configuration against required keys and types."""
        defaults = self.get_default_config()
        current = config.get_all()
        
        missing = []
        type_mismatches = []
        
        def check_recursive(d_dict, c_dict, path=""):
            for k, v in d_dict.items():
                current_path = f"{path}.{k}" if path else k
                if k not in c_dict:
                    missing.append(current_path)
                    continue
                
                if isinstance(v, dict) and isinstance(c_dict[k], dict):
                    check_recursive(v, c_dict[k], current_path)
                elif v is not None and c_dict[k] is not None:
                    if not isinstance(c_dict[k], type(v)):
                        type_mismatches.append(
                            f"{current_path}: expected {type(v).__name__}, got {type(c_dict[k]).__name__}"
                        )
        
        check_recursive(defaults, current)
        
        if missing:
            logger.warning(f"Missing configuration keys: {', '.join(missing)}")
            # For some critical keys we might want to raise an error, 
            # but for now we'll just log warnings as defaults are applied by the config util.
            
        if type_mismatches:
            error_msg = f"Configuration type mismatches: {', '.join(type_mismatches)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def setup_logging(self) -> None:
        """Setup logging with configured settings."""
        log_config = config.get("logging", {})
        media_config = config.get("media", {})

        # Use home folder logs dir or fallback to project logs
        log_dir = media_config.get("logs_dir", os.path.join(project_root, "logs"))
        
        # Expand ~ to home directory
        log_dir = os.path.expanduser(log_dir)

        logger.setup(
            log_dir=log_dir,
            level=log_config.get("level", "INFO"),
            max_bytes=log_config.get("max_bytes", 10485760),
            backup_count=log_config.get("backup_count", 5),
            zip_logs=log_config.get("zip_logs", True),
            rotate=log_config.get("rotate", True),
        )

    def setup_utilities(self) -> None:
        """Setup validator, version and encryption utilities."""
        validator.setup()

        versioning_config = config.get("versioning", {})
        version.setup(
            current_version=VERSION,
            min_supported_version=versioning_config.get(
                "min_supported_version", VERSION
            ),
        )

        # Initialize encryption with config values
        from src.utils import encryption
        encryption_config = config.get("encryption", {})
        encryption.setup(
            worker_id=encryption_config.get("snowflake", {}).get("worker_id", 1) or 1,
            datacenter_id=encryption_config.get("snowflake", {}).get("datacenter_id", 1) or 1,
            argon2_time_cost=encryption_config.get("argon2", {}).get("time_cost", 2),
            argon2_memory_cost=encryption_config.get("argon2", {}).get("memory_cost", 65536),
            argon2_parallelism=encryption_config.get("argon2", {}).get("parallelism", 2),
        )

    def initialize_modules(
        self,
    ) -> Tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any]:
        """Initialize all core modules in dependency order."""
        from src.core.database import Database, setup_redis
        from src.core import (
            auth,
            messaging,
            servers,
            relationships,
            presence,
            reactions,
            embeds,
            webhooks,
            settings,
            media,
            events,
        )
        from src.core import voice, notifications, threads
        from src.core.voice import signaling
        import src.api as api

        # Generate and set internal secret for secure self-test validation
        internal_secret = secrets.token_hex(32)
        api.set_internal_secret(internal_secret)
        logger.info("Internal security secret generated for self-test")

        # Persist long-lived secrets if they don't exist
        app_config = config.get("applications", {})
        if not app_config.get("webhook_signature_secret"):
            app_config["webhook_signature_secret"] = secrets.token_hex(32)
            config.set("applications", app_config)
            logger.info("Generated and persisted webhook_signature_secret")

        rl_config = config.get("rate_limiting", {})
        if not rl_config.get("bypass_secret"):
            rl_config["bypass_secret"] = secrets.token_hex(32)
            config.set("rate_limiting", rl_config)
            logger.info("Generated and persisted rate-limit bypass_secret")

        failed_modules = []

        # Initialize database
        logger.info("Initializing database...")
        self.db = Database()

        # Run migrations
        from src.core.migrations import run_migrations

        try:
            migration_result = run_migrations(self.db)
            if migration_result['success']:
                logger.info(
                    f"Migrations applied successfully: "
                    f"{migration_result['applied_count']} applied, "
                    f"{migration_result['failed_count']} failed"
                )
            else:
                logger.error(
                    f"Migration process had failures: "
                    f"{migration_result['applied_count']} applied, "
                    f"{migration_result['failed_count']} failed"
                )
        except Exception as e:
            logger.error(f"Critical error during migrations: {e}")
            raise
        
        self.db.connect()

        # Initialize Redis if enabled
        redis_config = config.get("redis") or {}
        if redis_config.get("enabled", False):
            logger.info("Initializing Redis...")
            redis_client = setup_redis()
            if redis_client and redis_client.ping():
                logger.info(
                    f"Connected to Redis at {redis_config.get('host', 'localhost')}:{redis_config.get('port', 6379)}"
                )
            else:
                logger.warning(
                    "Redis is enabled but connection failed - continuing without Redis"
                )
        else:
            logger.info("Redis is disabled in configuration")

        # Check encryption key rotation
        try:
            from src.utils import encryption

            if encryption.rotate_keys():
                logger.info("Encryption keys rotated successfully")
        except Exception as e:
            logger.error(f"Failed to check encryption key rotation: {e}")

        # Initialize core modules in dependency order
        startup_times = {}
        
        def timed_init(name: str, init_func):
            """Initialize a module and track how long it takes."""
            start = time.perf_counter()
            logger.info(f"Initializing {name} module...")
            try:
                result = init_func()
                elapsed = (time.perf_counter() - start) * 1000
                startup_times[name] = elapsed
                logger.info(f"  -> {name} initialized in {elapsed:.1f}ms")
                return result
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                startup_times[name] = elapsed
                logger.error(f"  -> {name} FAILED after {elapsed:.1f}ms: {e}")
                raise
        
        # Initialize email sender if configured
        email_config = config.get("email", {})
        email_sender = None
        smtp_password = os.getenv("PLEXICHAT_SMTP_PASSWORD")
        if email_config.get("smtp_host") and smtp_password:
            from src.utils.email import SMTPEmailSender
            email_sender = SMTPEmailSender(
                host=email_config["smtp_host"],
                port=email_config.get("smtp_port", 587),
                user=email_config.get("smtp_user", ""),
                password=smtp_password,
                from_email=email_config.get("from_email", "noreply@plexichat.internal"),
                use_tls=email_config.get("use_tls", True)
            )
            logger.info(f"Email sender initialized via SMTP ({email_config['smtp_host']})")
        else:
            logger.info("Email sender not initialized (SMTP host or PLEXICHAT_SMTP_PASSWORD missing)")

        timed_init("auth", lambda: auth.setup(self.db, email_sender=email_sender))
        self._modules["auth"] = auth

        timed_init("messaging", lambda: messaging.setup(self.db, auth))
        self._modules["messaging"] = messaging

        timed_init("servers", lambda: servers.setup(self.db, auth, messaging))
        self._modules["servers"] = servers

        timed_init("relationships", lambda: relationships.setup(self.db, auth, servers))
        self._modules["relationships"] = relationships

        timed_init("presence", lambda: presence.setup(self.db, auth, relationships, servers))
        self._modules["presence"] = presence

        timed_init("reactions", lambda: reactions.setup(self.db, messaging, servers, relationships))
        self._modules["reactions"] = reactions

        timed_init("embeds", lambda: embeds.setup(self.db, messaging, servers))
        self._modules["embeds"] = embeds

        timed_init("events", lambda: events.setup(
            relationships_module=relationships,
            servers_module=servers,
            messaging_module=messaging,
        ))
        self._modules["events"] = events

        timed_init("webhooks", lambda: webhooks.setup(self.db, auth, messaging, servers, embeds))
        self._modules["webhooks"] = webhooks

        timed_init("settings", lambda: settings.setup(self.db))
        self._modules["settings"] = settings

        try:
            timed_init("notifications", lambda: notifications.setup(
                self.db,
                auth_module=auth,
                messaging_module=messaging,
                servers_module=servers,
                relationships_module=relationships,
                presence_module=presence,
            ))
            self._modules["notifications"] = notifications
        except Exception as e:
            logger.warning(f"Failed to initialize notifications module: {e}")
            failed_modules.append("notifications")

        try:
            timed_init("threads", lambda: threads.setup(self.db, messaging, servers))
            self._modules["threads"] = threads
        except Exception as e:
            logger.warning(f"Failed to initialize threads module: {e}")
            failed_modules.append("threads")

        # Initialize user features module
        features = None
        try:
            from src.core import features
            timed_init("features", lambda: features.setup(self.db))
            self._modules["features"] = features
        except Exception as e:
            logger.warning(f"Failed to initialize user features module: {e}")
            failed_modules.append("features")

        timed_init("media", lambda: media.setup(self.db, messaging))
        self._modules["media"] = media

        # Initialize avatars module
        try:
            from src.core import avatars
            timed_init("avatars", lambda: avatars.setup(self.db))
            self._modules["avatars"] = avatars
        except Exception as e:
            logger.warning(f"Failed to initialize avatars module: {e}")
            failed_modules.append("avatars")

        # Initialize voice module
        try:
            timed_init("voice", lambda: voice.setup(self.db, auth, servers, relationships, presence))
            self._modules["voice"] = voice
        except Exception as e:
            logger.warning(f"Failed to initialize voice module: {e}")
            failed_modules.append("voice")

        # Initialize voice signaling with SFU configuration
        voice_config = config.get("voice") or {}
        if voice_config.get("enabled", False):
            try:
                sfu_backend = voice_config.get("sfu_backend", "mediasoup")
                def init_signaling():
                    signaling.setup(
                        voice_module=voice,
                        events_module=events,
                        sfu_backend=sfu_backend,
                        mediasoup_url=voice_config.get(
                            "mediasoup_url", "https://localhost:4443"
                        ),
                        mediasoup_origin=voice_config.get(
                            "mediasoup_origin",
                            "https://plexichat-app.tail79f345.ts.net:8443",
                        ),
                        janus_url=voice_config.get(
                            "janus_url", "http://localhost:8088/janus"
                        ),
                        stun_urls=voice_config.get(
                            "stun_urls", ["stun:stun.l.google.com:19302"]
                        ),
                        turn_urls=voice_config.get("turn_urls", []),
                        turn_secret=voice_config.get("turn_secret", ""),
                        turn_ttl=voice_config.get("turn_ttl", 86400),
                        turn_username=voice_config.get("turn_username", ""),
                        turn_credential=voice_config.get("turn_credential", ""),
                    )
                timed_init("signaling", init_signaling)
                self._modules["signaling"] = signaling
                sfu_url = (
                    voice_config.get("mediasoup_url")
                    if sfu_backend in ("mediasoup", "mediasoup-ws")
                    else voice_config.get("janus_url")
                )
                logger.info(
                    f"Voice signaling initialized with {sfu_backend} backend at {sfu_url}"
                )
                if voice_config.get("turn_urls"):
                    logger.info(
                        f"TURN servers configured: {len(voice_config.get('turn_urls', []))} servers"
                    )
                if voice_config.get("log_connections", False):
                    logger.info("Voice connection logging enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize voice signaling module: {e}")
                failed_modules.append("signaling")
        else:
            logger.info("Voice module disabled in configuration")

        # Initialize rate limit module
        rate_limit_settings = config.get("rate_limiting", {})
        if rate_limit_settings.get("enabled", True):
            try:
                from src.core import ratelimit
                from src.core.ratelimit.storage import (
                    RedisStorage,
                    MemoryStorage,
                    DatabaseStorage,
                )
                from src.core.database.redis_client import (
                    is_available as redis_is_available,
                )

                def init_ratelimit():
                    storage = None
                    if redis_is_available():
                        storage = RedisStorage()
                        logger.info("Using Redis storage for rate limiting")
                    elif self.db:
                        storage = DatabaseStorage(self.db)
                        db_type = self.db.type.capitalize()
                        logger.info(
                            f"Using {db_type} storage for rate limiting (shared across workers)"
                        )
                    else:
                        storage = MemoryStorage()
                        logger.info("Using in-memory storage for rate limiting (fallback)")

                    ratelimit.setup(
                        storage_backend=storage,
                        bot_multiplier=rate_limit_settings.get("bot_multiplier", 1.5),
                        webhook_multiplier=rate_limit_settings.get(
                            "webhook_multiplier", 1.0
                        ),
                        enable_global_limit=True,
                    )
                timed_init("ratelimit", init_ratelimit)
                self._modules["ratelimit"] = ratelimit
            except Exception as e:
                logger.warning(f"Failed to initialize rate limit module: {e}")
                failed_modules.append("ratelimit")

        # Initialize telemetry module
        telemetry_config = config.get("telemetry") or {}
        if telemetry_config.get("enabled", True):
            try:
                from src.core import telemetry
                timed_init("telemetry", lambda: telemetry.setup(self.db))
                self._modules["telemetry"] = telemetry
            except Exception as e:
                logger.warning(f"Failed to initialize telemetry module: {e}")
                failed_modules.append("telemetry")

        # Initialize feedback module
        feedback_config = config.get("feedback") or {}
        if feedback_config.get("enabled", True):
            try:
                from src.core import feedback
                timed_init("feedback", lambda: feedback.setup(self.db))
                self._modules["feedback"] = feedback
            except Exception as e:
                logger.warning(f"Failed to initialize feedback module: {e}")
                failed_modules.append("feedback")

        # Initialize reports module
        reports_config = config.get("reports") or {}
        if reports_config.get("enabled", True):
            try:
                from src.core import reports
                timed_init("reports", lambda: reports.setup(self.db, messaging))
                self._modules["reports"] = reports
            except Exception as e:
                logger.warning(f"Failed to initialize reports module: {e}")
                failed_modules.append("reports")

        # Initialize admin module
        admin_config = config.get("admin_ui") or {}
        if admin_config.get("enabled", True):
            try:
                from src.core import admin
                timed_init("admin", lambda: admin.setup(self.db, auth, features))
                self._modules["admin"] = admin
                logger.info(
                    f"Admin module initialized (path: {admin_config.get('path', '/admin')})"
                )
            except Exception as e:
                logger.warning(f"Failed to initialize admin module: {e}")
                failed_modules.append("admin")

        # Log summary of initialization with timing
        total_time = sum(startup_times.values())
        logger.info("=" * 60)
        logger.info("MODULE INITIALIZATION SUMMARY")
        logger.info("=" * 60)
        for module_name, elapsed in sorted(startup_times.items(), key=lambda x: -x[1]):
            logger.info(f"  {module_name:20s} {elapsed:8.1f}ms")
        logger.info("-" * 60)
        logger.info(f"  {'TOTAL':20s} {total_time:8.1f}ms")
        logger.info("=" * 60)
        
        if failed_modules:
            logger.error(
                f"Module initialization incomplete. Failed modules: {', '.join(failed_modules)}"
            )
        else:
            logger.info("All modules initialized successfully")

        return (
            auth,
            messaging,
            servers,
            relationships,
            presence,
            reactions,
            embeds,
            webhooks,
            settings,
            media,
        )

    def create_application(
        self,
        auth: Any,
        messaging: Any,
        servers: Any,
        relationships: Any,
        presence: Any,
        reactions: Any,
        embeds: Any,
        webhooks: Any,
        settings: Any,
        media: Any,
    ) -> Any:
        """Create and configure the FastAPI application."""
        from src.api import setup as api_setup, create_app

        start = time.perf_counter()
        logger.info("Initializing API module...")
        api_setup(
            db=self.db,
            auth_module=auth,
            messaging_module=messaging,
            servers_module=servers,
            relationships_module=relationships,
            presence_module=presence,
            reactions_module=reactions,
            embeds_module=embeds,
            webhooks_module=webhooks,
            settings_module=settings,
            media_module=media,
            features_module=self._modules.get("features"),
            avatars_module=self._modules.get("avatars"),
            reports_module=self._modules.get("reports"),
            feedback_module=self._modules.get("feedback"),
            admin_module=self._modules.get("admin"),
            events_module=self._modules.get("events"),
            notifications_module=self._modules.get("notifications"),
            threads_module=self._modules.get("threads"),
            telemetry_module=self._modules.get("telemetry"),
        )

        # Get rate limiting and docs settings from config
        rate_limit_config = config.get("rate_limiting", {})
        docs_config = config.get("docs", {})
        enable_rate_limiting = rate_limit_config.get("enabled", True)
        enable_docs = docs_config.get("enabled", True)

        self.app = create_app(
            enable_rate_limiting=enable_rate_limiting, enable_docs=enable_docs
        )
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(f"  -> API initialized in {elapsed:.1f}ms")
        return self.app

    async def notify_clients_shutdown(self, message: str = "Server shutting down"):
        """Notify connected WebSocket clients about shutdown."""
        try:
            from src.api import websocket

            if websocket.is_setup():
                await websocket.broadcast_server_status(
                    {"state": "shutting_down", "message": message, "restart_at": None}
                )
                logger.info("Notified clients of shutdown")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")

    async def notify_clients_restart(self, estimated_seconds: int = 10):
        """Notify connected WebSocket clients about restart."""
        try:
            from src.api import websocket

            if websocket.is_setup():
                await websocket.broadcast_server_status(
                    {
                        "state": "restarting",
                        "message": "Server is restarting",
                        "estimated_downtime_seconds": estimated_seconds,
                    }
                )
                logger.info("Notified clients of restart")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")

    def cleanup(self) -> None:
        """Clean up resources on shutdown."""
        logger.info("Cleaning up resources...")

        # Close all WebSocket connections gracefully
        try:
            from src.api import websocket

            if websocket.is_setup():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    closed = loop.run_until_complete(
                        websocket.close_all_connections(
                            reason="Server shutting down",
                            notify_first=False,  # Already notified in signal handler
                            grace_period_seconds=0.5,
                        )
                    )
                    logger.info(f"Closed {closed} WebSocket connections")
                finally:
                    loop.close()
        except Exception as e:
            logger.debug(f"Error closing WebSocket connections: {e}")

        # Close database connection
        if self.db:
            try:
                self.db.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")

        logger.info("Cleanup complete")

    def run(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        """Run the server with graceful shutdown support."""
        import uvicorn

        server_config = config.get("server", {})
        host = host or server_config.get("host", "0.0.0.0")
        port = port or server_config.get("port", 8000)

        # Check TLS configuration
        ssl_config = {}
        tls_config = config.get("tls", {})
        use_https = False

        if tls_config.get("enabled", False) or tls_config.get(
            "auto_generate_self_signed", False
        ):
            try:
                from src.core import tls

                if tls.is_tls_enabled():
                    ssl_config = tls.get_tls_config()
                    if ssl_config:
                        use_https = True
                        logger.info("TLS enabled - server will use HTTPS")
            except ImportError:
                logger.warning("TLS module not available")
            except Exception as e:
                logger.error(f"Failed to configure TLS: {e}")

        # Create uvicorn config
        assert self.app is not None
        assert host is not None
        assert port is not None

        uvi_config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="info",
            loop="asyncio",
            **ssl_config,
        )

        self.server = uvicorn.Server(uvi_config)
        assert self.server is not None

        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            signal_name = "SIGINT" if signum == signal.SIGINT else f"signal {signum}"
            logger.info(f"Received {signal_name}, initiating graceful shutdown...")
            self.shutdown_event.set()

            # Run async notification in event loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Schedule the notification coroutine
                asyncio.run_coroutine_threadsafe(self.notify_clients_shutdown(), loop)

            if self.server:
                self.server.should_exit = True

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)

        # SIGTERM is not available on Windows, only register if supported
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, signal_handler)

        logger.info("All modules initialized successfully")
        logger.info("=" * 60)

        # Get version string from version utility
        ver = version.current_string()
        protocol = "https" if use_https else "http"
        server_line = f"Server: {protocol}://{host}:{port}"
        api_line = f"API:    {protocol}://{host}:{port}/api/v1"
        docs_line = f"Docs:   {protocol}://{host}:{port}/docs"
        tls_line = "TLS:    Enabled (self-signed)" if use_https else "TLS:    Disabled"

        print(f"""
+==============================================================+
|                    PlexiChat API Server                      |
|                      Version {ver:<32}|
+==============================================================+
|  {server_line:<60}|
|  {api_line:<60}|
|  {docs_line:<60}|
|  {tls_line:<60}|
+--------------------------------------------------------------+
|  Press Ctrl+C for graceful shutdown                          |
|  Data stored in: ~/.plexichat/                               |
+==============================================================+
        """)

        # Run the server
        self.server.run()

        # Cleanup after server stops
        self.cleanup()

        return self.restart_requested

    def request_restart(self) -> None:
        """Request a server restart."""
        self.restart_requested = True
        self.shutdown_event.set()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.notify_clients_restart())
        except Exception as e:
            logger.warning(f"Failed to notify clients of restart: {e}")

        if self.server:
            self.server.should_exit = True


def main() -> None:
    """Main entry point for the PlexiChat server."""
    parser = argparse.ArgumentParser(
        description="PlexiChat API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                 # Start server normally
  python main.py --self-test     # Run API self-test and exit
  python main.py --create-config # Generate default config file
  python main.py --config custom.yaml # Use custom config file
        """,
    )

    parser.add_argument("--config", help="Path to custom configuration file")
    parser.add_argument(
        "--create-config",
        action="store_true",
        help="Create default config file and exit",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Run automated API self-test and exit"
    )
    parser.add_argument("--host", help="Override server host")
    parser.add_argument("--port", type=int, help="Override server port")
    parser.add_argument("--version", action="store_true", help="Show version and exit")

    # Use parse_known_args to ignore uvicorn args if wrapped
    args, _ = parser.parse_known_args()

    if args.version:
        print(f"PlexiChat Server v{VERSION}")
        return

    server = PlexiChatServer()

    # Handle --create-config
    if args.create_config:
        config_dir = os.path.join(project_root, "config")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "config.yaml")

        if os.path.exists(config_path):
            print(f"Config file already exists at {config_path}")
            return

        import yaml

        with open(config_path, "w") as f:
            yaml.dump(server.get_default_config(), f, default_flow_style=False)
        print(f"Created default configuration at {config_path}")
        return

    # Buffer for early log messages (before logger is configured)
    early_logs = []

    # Setup directories in home folder first (buffer logs)
    home_dir = Path.home() / ".plexichat"
    dirs = ["data", "logs", "media", "temp", "config"]
    for d in dirs:
        dir_path = home_dir / d
        dir_path.mkdir(parents=True, exist_ok=True)
        early_logs.append(("debug", f"Ensured directory exists: {dir_path}"))

    # Setup configuration (needed for logger config)
    # Priority: 1. --config arg, 2. PLEXICHAT_CONFIG env var, 3. auto-detect
    config_path = None
    config_source = None
    
    if args.config:
        config_path = args.config
        config_source = "command line argument"
    elif os.environ.get("PLEXICHAT_CONFIG"):
        config_path = os.environ.get("PLEXICHAT_CONFIG")
        config_source = "PLEXICHAT_CONFIG environment variable"
    
    if config_path:
        config.setup(
            config_path=config_path, default_config=server.get_default_config()
        )
        early_logs.append(("info", f"Config loaded from {config_source}: {config_path}"))
    else:
        config_path = server.setup_config()
        early_logs.append(("info", f"Config loaded from auto-detected path: {config_path}"))

    # Setup logging - NOW we can use logger
    log_config = config.get("logging", {})
    media_config = config.get("media", {})
    log_dir = media_config.get("logs_dir", os.path.join(project_root, "logs"))
    log_dir = os.path.expanduser(log_dir)  # Expand ~ to home directory
    
    early_logs.append(("info", "Initializing logger..."))
    early_logs.append(("info", f"  Log directory: {log_dir}"))
    early_logs.append(("info", f"  Log level: {log_config.get('level', 'INFO')}"))
    
    server.setup_logging()

    # Replay buffered logs
    for level, msg in early_logs:
        getattr(logger, level)(msg)

    # Now we can log normally
    logger.info("=" * 60)
    logger.info("PlexiChat Server Starting")
    logger.info("=" * 60)

    # Log startup info
    app_config = config.get("application", {})
    logger.info(f"Application: {app_config.get('name', 'PlexiChat')}")
    logger.info(f"Version: {VERSION}")
    logger.info(f"Environment: {app_config.get('environment', 'development')}")
    logger.info(f"Config file: {config_path}")

    # Security warnings for default/placeholder keys
    _check_security_keys()

    # Validate configuration
    try:
        server.validate_config()
        logger.info("Configuration validated successfully")
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        # Default to strict config unless explicitly disabled
        if os.getenv("NO_STRICT_CONFIG") != "true":
            sys.exit(1)

    # Setup utilities
    server.setup_utilities()

    # Initialize modules
    modules = server.initialize_modules()

    # Create application
    server.create_application(*modules)

    # Get server settings
    host = args.host or os.getenv(
        "HOST", config.get("server", {}).get("host", "0.0.0.0")
    )
    port = args.port or int(
        os.getenv("PORT", config.get("server", {}).get("port", 8000))
    )

    # Handle self-test
    if args.self_test:
        logger.info("Starting Self-Test Mode...")
        # Override host to localhost for safety
        host = "127.0.0.1"

        # Set log level to INFO for cleaner output during self-test
        import logging

        logging.getLogger("AppLogger").setLevel(logging.INFO)

        # Start server in background thread
        import uvicorn

        if server.app is None:
            logger.error("Failed to create FastAPI application for self-test")
            sys.exit(1)

        uvi_config = uvicorn.Config(server.app, host=host, port=port, log_level="error")
        uvi_server = uvicorn.Server(uvi_config)

        server_thread = threading.Thread(target=uvi_server.run, daemon=True)
        server_thread.start()

        # Wait for server to start
        import time

        time.sleep(2)

        # Run self-test
        try:
            from src.core.selftest.runner import SelfTestRunner

            runner = SelfTestRunner(base_url=f"http://{host}:{port}")
            success = runner.run_all()

            # Stop server and exit
            uvi_server.should_exit = True
            server.cleanup()
            sys.exit(0 if success else 1)
        except Exception as e:
            logger.error(f"Self-test execution failed: {e}", exc_info=True)
            sys.exit(1)

    # Run server (returns True if restart was requested)
    should_restart = server.run(host, port)

    if should_restart:
        logger.info("Restart requested, restarting server...")
        # Re-execute the script
        os.execv(sys.executable, [sys.executable] + sys.argv)

    logger.info("Server shutdown complete")


def _check_security_keys() -> None:
    """Check for default/placeholder security keys and warn if found."""
    warnings = []

    # Check media signing key
    media_config = config.get("media", {})
    signing_key = media_config.get("signing_key", "")
    if signing_key in ["", "CHANGE_THIS_SIGNING_KEY", "change-me", "changeme"]:
        warnings.append("media.signing_key is using a default/placeholder value")

    # Check Redis password (if enabled)
    redis_config = config.get("redis", {})
    if redis_config.get("enabled", False):
        redis_pass = redis_config.get("password", "")
        if not redis_pass:
            warnings.append("redis.password is empty (Redis is enabled)")

    # Check database password (if PostgreSQL)
    db_config = config.get("database", {})
    if db_config.get("type") == "postgres":
        pg_config = db_config.get("postgres", {})
        pg_pass = pg_config.get("password", "")
        if not pg_pass or pg_pass in ["password", "postgres", "changeme"]:
            warnings.append("database.postgres.password is using a weak/default value")

    # Check TURN secret (if voice enabled with TURN)
    voice_config = config.get("voice", {})
    if voice_config.get("enabled", False) and voice_config.get("turn_urls"):
        turn_secret = voice_config.get("turn_secret", "")
        if not turn_secret:
            warnings.append("voice.turn_secret is empty (TURN is configured)")

    # Check message encryption key (auto-generated)
    messaging_config = config.get("messaging", {})
    if messaging_config.get("encrypt_messages", True):
        try:
            from src.utils.encryption import is_message_key_auto_generated

            if is_message_key_auto_generated():
                warnings.append(
                    "MESSAGE ENCRYPTION: Using auto-generated key. "
                    "Set PLEXICHAT_MESSAGE_KEY env var or back up ~/.plexichat/data/message_keyring.json"
                )
        except Exception:
            warnings.append(
                "messaging.encrypt_messages is enabled (ensure PLEXICHAT_MESSAGE_KEY is set or back up message_keyring.json)"
            )

    # Log warnings
    if warnings:
        logger.warning("=" * 60)
        logger.warning("SECURITY WARNING: Default/placeholder keys detected!")
        logger.warning("=" * 60)
        for warning in warnings:
            logger.warning(f"  - {warning}")
        logger.warning(
            "Please update these values in your config file for production use."
        )
        logger.warning("=" * 60)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()

