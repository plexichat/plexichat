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
from pathlib import Path
from typing import Optional

# Setup paths
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

# Import utilities
import utils.logger as logger
import utils.config as config
import utils.validator as validator
import utils.version as version

# Global Version Definition
VERSION = "a.1.0-15"


class PlexiChatServer:
    """Main server class with lifecycle management."""
    
    def __init__(self):
        self.app = None
        self.server = None
        self.db = None
        self.shutdown_event = threading.Event()
        self.restart_requested = False
        self._modules = {}
    
    def get_default_config(self) -> dict:
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
                "include_exception_details": True
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
                    "sslmode": "prefer"
                },
                "connection_pool": {
                    "min_connections": 1,
                    "max_connections": 10
                }
            },
            "authentication": {
                "accounts": {
                    "allow_registration": True,
                    "require_email_verification": False,
                    "max_bots_per_user": 5,
                    "username_min_length": 3,
                    "username_max_length": 32
                },
                "sessions": {
                    "token_bytes": 32,
                    "expire_hours": 168,
                    "max_per_user": 10,
                    "extend_on_activity": True,
                    "extend_threshold_hours": 24
                },
                "security": {
                    "max_failed_attempts": 5,
                    "lockout_duration_minutes": 15,
                    "token_cache_ttl": 30,
                    "token_verify_rate_limit": 100,
                    "token_binding": False
                },
                "totp": {
                    "issuer": "PlexiChat",
                    "digits": 6,
                    "interval": 30,
                    "backup_code_count": 10
                },
                "password": {
                    "min_length": 12,
                    "max_length": 128,
                    "require_uppercase": True,
                    "require_lowercase": True,
                    "require_digit": True,
                    "require_special": True
                },
                "bots": {
                    "token_bytes": 48,
                    "require_owner_2fa": False
                }
            },
            "encryption": {
                "argon2": {
                    "time_cost": 2,
                    "memory_cost": 65536,
                    "parallelism": 2,
                    "hash_length": 32,
                    "salt_length": 16
                },
                "aes_gcm": {
                    "key_length": 32,
                    "nonce_length": 12,
                    "tag_length": 16
                },
                "snowflake": {
                    "epoch": "2024-01-01T00:00:00Z",
                    "worker_id": 1,
                    "datacenter_id": 1
                }
            },
            "redis": {
                "enabled": False,
                "host": "localhost",
                "port": 6379,
                "password": "",
                "db": 0,
                "ssl": False,
                "key_prefix": "plexichat:",
                "connection_pool": {
                    "max_connections": 50,
                    "timeout": 5
                },
                "ttl": {
                    "session": 1800,
                    "presence": 300,
                    "cache": 60
                }
            },
            "api": {
                "title": "PlexiChat API",
                "description": "REST API for PlexiChat messaging platform",
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
                    "http://192.168.3.221:5000",
                    "http://192.168.3.221:8000"
                ],
                "cors_allow_credentials": True,
                "cors_allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                "cors_allow_headers": ["Authorization", "Content-Type", "X-Requested-With", "Accept", "Origin"],
                "docs_url": "/docs",
                "redoc_url": "/redoc",
                "openapi_url": "/openapi.json"
            },
            "server": {
                "host": "0.0.0.0",
                "port": 8000,
                "workers": 1,
                "reload": False
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
                "allowed_origins": []
            },
            "application": {
                "name": "PlexiChat",
                "version": VERSION,
                "environment": "development"
            },
            "versioning": {
                "min_supported_version": VERSION,
                "update_url": None
            },
            "storage": {
                "data_dir": str(home_dir / "data"),
                "logs_dir": str(home_dir / "logs"),
                "media_dir": str(home_dir / "media"),
                "temp_dir": str(home_dir / "temp")
            },
            "rate_limiting": {
                "enabled": True,
                # Global rate limit (per user, across all requests)
                "global": {
                    "requests": 50,
                    "window_seconds": 1.0,
                    "burst": 10
                },
                # Per-user general limit
                "user": {
                    "requests": 120,
                    "window_seconds": 60.0,
                    "burst": 20,
                    "hourly_limit": 3600,
                    "daily_limit": 50000
                },
                # Per-IP limit (for unauthenticated requests)
                "ip": {
                    "requests": 60,
                    "window_seconds": 60.0,
                    "burst": 10,
                    "hourly_limit": 1800,
                    "daily_limit": 10000
                },
                # Bot multiplier (bots get this multiplier on limits)
                "bot_multiplier": 1.5,
                # Webhook multiplier
                "webhook_multiplier": 1.0,
                # Bypass for admins/internal requests
                "admin_bypass": True,
                "internal_bypass": True
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
                    "border_color": "#0f3460"
                },
                "rate_limit": {
                    "enabled": True,
                    "requests": 60,
                    "window_seconds": 60
                },
                "cache": {
                    "enabled": True,
                    "ttl_seconds": 300
                },
                "security": {
                    "require_auth": False
                }
            },
            "feedback": {
                "enabled": True,
                "rate_limit": {
                    "max_per_hour": 5,
                    "max_per_day": 20
                }
            },
            "media": {
                # Primary storage backend: "local", "s3", or "database"
                "storage_backend": "local",
                
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
                    "enabled": False,
                    "max_size": 524288,  # Files under 512KB
                    "content_types": [  # Only these types get routed to DB
                        "text/plain",
                        "application/json",
                        "text/markdown",
                        "text/csv"
                    ]
                },
                
                # File size limits per media type (in bytes)
                "size_limits": {
                    "image": 10485760,      # 10MB
                    "video": 104857600,     # 100MB
                    "audio": 52428800,      # 50MB
                    "document": 26214400,   # 25MB
                    "icon": 2097152,        # 2MB (server/user icons)
                    "avatar": 5242880,      # 5MB (user avatars)
                    "other": 10485760       # 10MB
                },
                
                # Allowed content types per media type
                "allowed_types": {
                    "image": [
                        "image/jpeg",
                        "image/png",
                        "image/gif",
                        "image/webp"
                    ],
                    "video": [
                        "video/mp4",
                        "video/webm",
                        "video/quicktime"
                    ],
                    "audio": [
                        "audio/mpeg",
                        "audio/ogg",
                        "audio/wav",
                        "audio/webm"
                    ],
                    "document": [
                        "application/pdf",
                        "text/plain",
                        "application/zip",
                        "text/markdown",
                        "application/json"
                    ]
                },
                
                # Thumbnail generation
                "thumbnail_sizes": [64, 128, 256, 512],
                "image_quality": 85,
                "image_optimize": True,
                
                # Image processing security limits (DoS protection)
                "image_processing": {
                    "max_dimension": 16384,  # Max width/height in pixels
                    "max_pixels": 178956970,  # Max total pixels (~13400x13400)
                    "max_thumbnail_requests_per_minute": 60  # Rate limit for thumbnail generation
                },
                
                # Video processing security limits
                "video_processing": {
                    "ffprobe_timeout": 30,  # Timeout in seconds for ffprobe
                    "max_size_for_metadata": 524288000  # 500MB max for metadata extraction
                },
                
                # URL signing for secure access
                "signing_key": "CHANGE_THIS_SIGNING_KEY",
                "signing_expiry": 3600,  # 1 hour
                
                # Malware scanning (ClamAV)
                "scanner_enabled": False,
                "scanner_host": "localhost",
                "scanner_port": 3310,
                
                # External URL proxy
                "proxy_enabled": True,
                "proxy_cache_ttl": 86400,  # 24 hours
                "proxy_max_size": 10485760,  # 10MB
                
                # Rate limiting for uploads
                "rate_limit": {
                    "enabled": True,
                    "uploads_per_minute": 10,
                    "uploads_per_hour": 100,
                    "max_total_size_per_day": 536870912  # 512MB per user per day
                }
            },
            # Avatars configuration (user avatars and server icons stored in database)
            "avatars": {
                # Maximum dimension for avatars (width and height)
                "max_size": 512,
                # Maximum file size before processing (5MB)
                "max_file_size": 5242880,
                # Allowed content types
                "allowed_types": [
                    "image/jpeg",
                    "image/png",
                    "image/gif",
                    "image/webp"
                ]
            },
            # Telemetry configuration
            "telemetry": {
                "enabled": True,
                "rate_limit": {
                    "max_per_minute": 10
                },
                "retention_days": 30
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
                    "stun:stun3.l.google.com:19302"
                ],
                # TURN servers for relay (required for restrictive NATs/firewalls)
                # Using metered.ca free TURN servers - replace with your own coturn for production
                "turn_urls": [
                    "turn:openrelay.metered.ca:80",
                    "turn:openrelay.metered.ca:443",
                    "turns:openrelay.metered.ca:443"
                ],
                # Static TURN credentials (for metered.ca free tier)
                # For coturn with time-limited credentials, use turn_secret instead
                "turn_username": "openrelayproject",
                "turn_credential": "openrelayproject",
                # TURN secret for time-limited credentials (coturn static-auth-secret)
                "turn_secret": "",
                "turn_ttl": 86400,
                "log_connections": False
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
                    "allowed_hosts": ["127.0.0.1", "localhost", "::1"]
                },
                # Allowed origins for admin panel CORS (empty = use main api.cors_origins)
                "allowed_origins": [],
                # Rate limiting for admin login attempts
                "rate_limit": {
                    "max_attempts": 5,
                    "window_seconds": 300,
                    "lockout_seconds": 900
                }
            },
            # TLS/HTTPS configuration
            "tls": {
                "enabled": False,
                "auto_generate_self_signed": False,
                "cert_path": str(home_dir / "certs" / "server.crt"),
                "key_path": str(home_dir / "certs" / "server.key"),
                "cert_days": 365
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
                    "org_root",
                    "verified",
                    "bug_hunter",
                    "contributor",
                    "moderator",
                    "partner"
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
                        "max_file_size_mb": 10,
                        "max_servers": 100,
                        "max_message_length": 2000,
                        "max_reactions_per_message": 20,
                        "max_pins_per_channel": 50,
                        "custom_emoji_slots": 50
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
                        "custom_emoji_slots": 100
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
                        "custom_emoji_slots": 250
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
                        "custom_emoji_slots": -1
                    }
                },
                # Rate limits for feature management endpoints (admin only)
                "admin_rate_limit": {
                    "max_per_minute": 30,
                    "max_per_hour": 200
                }
            },
            # Organizations configuration (for org ID feature)
            "organizations": {
                "enabled": True,
                "default_org_name": "default",
                "default_org_display_name": "PlexiChat",
                # Root user settings
                "root_user": {
                    "require_otp": True,
                    "can_reset_passwords": True,
                    "can_lock_accounts": True,
                    "can_manage_servers": True,
                    "can_view_audit_log": True,
                    "can_force_logout": True
                },
                # Settings that org root can lock for members
                "manageable_settings": [
                    "allow_dms",
                    "allow_friend_requests",
                    "show_activity",
                    "theme"
                ],
                # Default org behavior
                "default_org": {
                    "allow_users_to_leave": False,
                    "allow_users_to_join_orgs": True
                },
                # Invite settings
                "invites": {
                    "code_length": 32,
                    "default_expiry_hours": 168,
                    "require_two_step": True
                },
                # Server restrictions
                "server_restrictions": {
                    "enabled": True,
                    "default_mode": "none"
                },
                # Rate limits for org management endpoints
                "rate_limit": {
                    "max_invites_per_hour": 20,
                    "max_member_actions_per_minute": 10
                }
            }
        }
    
    def setup_directories(self):
        """Create necessary directories in home folder."""
        home_dir = Path.home() / ".plexichat"
        dirs = ["data", "logs", "media", "temp", "config"]
        
        for d in dirs:
            dir_path = home_dir / d
            dir_path.mkdir(parents=True, exist_ok=True)
        
        logger.debug(f"Created directories in {home_dir}")
    
    def setup_config(self):
        """Setup configuration from file or defaults, with environment variable overrides."""
        # Try project config first, then home folder config
        config_paths = [
            os.path.join(project_root, "config", "config.yaml"),
            Path.home() / ".plexichat" / "config" / "config.yaml"
        ]
        
        config_path = None
        for cp in config_paths:
            if os.path.exists(cp):
                config_path = str(cp)
                break
        
        if not config_path:
            config_path = str(config_paths[0])
        
        config.setup(config_path=config_path, default_config=self.get_default_config())
        
        # Apply environment variable overrides
        self._apply_env_overrides()
        
        return config_path
    
    def _apply_env_overrides(self):
        """Apply environment variable overrides to configuration."""
        # DATABASE_URL override (format: postgres://user:pass@host:port/dbname or sqlite:///path)
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            db_config = config.get("database", {})
            if database_url.startswith("postgres://") or database_url.startswith("postgresql://"):
                # Parse PostgreSQL URL
                import urllib.parse
                parsed = urllib.parse.urlparse(database_url)
                db_config["type"] = "postgres"
                db_config["postgres"] = {
                    "host": parsed.hostname or "localhost",
                    "port": parsed.port or 5432,
                    "user": parsed.username or "postgres",
                    "password": parsed.password or "",
                    "dbname": parsed.path.lstrip("/") if parsed.path else "plexichat",
                    "sslmode": "prefer"
                }
                # Parse query params for sslmode
                if parsed.query:
                    params = urllib.parse.parse_qs(parsed.query)
                    if "sslmode" in params:
                        db_config["postgres"]["sslmode"] = params["sslmode"][0]
            elif database_url.startswith("sqlite:///"):
                db_config["type"] = "sqlite"
                db_config["path"] = database_url[10:]  # Remove sqlite:///
            config.set("database", db_config)
        
        # LOG_LEVEL override
        log_level = os.getenv("LOG_LEVEL")
        if log_level:
            log_config = config.get("logging", {})
            log_config["level"] = log_level.upper()
            config.set("logging", log_config)

    def setup_logging(self):
        """Setup logging with configured settings."""
        log_config = config.get("logging", {})
        storage_config = config.get("storage", {})
        
        # Use home folder logs dir or fallback to project logs
        log_dir = storage_config.get("logs_dir", os.path.join(project_root, "logs"))
        
        logger.setup(
            log_dir=log_dir,
            level=log_config.get("level", "INFO"),
            max_bytes=log_config.get("max_bytes", 10485760),
            backup_count=log_config.get("backup_count", 5),
            zip_logs=log_config.get("zip_logs", True),
            rotate=log_config.get("rotate", True)
        )
    
    def setup_utilities(self):
        """Setup validator and version utilities."""
        validator.setup()
        
        versioning_config = config.get("versioning", {})
        version.setup(
            current_version=VERSION,
            min_supported_version=versioning_config.get("min_supported_version", VERSION),
        )
    
    def initialize_modules(self):
        """Initialize all core modules in dependency order."""
        from src.core.database import Database, setup_redis, get_redis_client
        from src.core import auth, messaging, servers, relationships, presence, reactions, embeds, webhooks, settings, media
        from src.core import voice
        from src.core.voice import signaling
        
        # Initialize database
        logger.info("Initializing database...")
        self.db = Database()
        self.db.connect()
        
        # Initialize Redis if enabled
        redis_config = config.get("redis") or {}
        if redis_config.get("enabled", False):
            logger.info("Initializing Redis...")
            redis_client = setup_redis()
            if redis_client and redis_client.ping():
                logger.info(f"Connected to Redis at {redis_config.get('host', 'localhost')}:{redis_config.get('port', 6379)}")
            else:
                logger.warning("Redis is enabled but connection failed - continuing without Redis")
        else:
            logger.info("Redis is disabled in configuration")
        
        # Initialize core modules in dependency order
        logger.info("Initializing auth module...")
        auth.setup(self.db)
        self._modules['auth'] = auth
        
        logger.info("Initializing messaging module...")
        messaging.setup(self.db, auth)
        self._modules['messaging'] = messaging
        
        logger.info("Initializing servers module...")
        servers.setup(self.db, auth, messaging)
        self._modules['servers'] = servers
        
        logger.info("Initializing relationships module...")
        relationships.setup(self.db, auth, servers)
        self._modules['relationships'] = relationships
        
        logger.info("Initializing presence module...")
        presence.setup(self.db, auth, relationships, servers)
        self._modules['presence'] = presence
        
        logger.info("Initializing reactions module...")
        reactions.setup(self.db, messaging, servers, relationships)
        self._modules['reactions'] = reactions
        
        logger.info("Initializing embeds module...")
        embeds.setup(self.db, messaging, servers)
        self._modules['embeds'] = embeds
        
        logger.info("Initializing webhooks module...")
        webhooks.setup(self.db, auth, messaging, servers, embeds)
        self._modules['webhooks'] = webhooks
        
        logger.info("Initializing settings module...")
        settings.setup(self.db)
        self._modules['settings'] = settings
        
        # Initialize user features module
        logger.info("Initializing user features module...")
        try:
            from src.core import features
            features.setup(self.db)
            self._modules['features'] = features
            logger.info("User features module initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize user features module: {e}")
        
        # Initialize organizations module
        orgs_config = config.get("organizations") or {}
        if orgs_config.get("enabled", True):
            logger.info("Initializing organizations module...")
            try:
                from src.core import organizations
                organizations.setup(self.db, auth)
                self._modules['organizations'] = organizations
                logger.info("Organizations module initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize organizations module: {e}")
        
        logger.info("Initializing media module...")
        media.setup(self.db, messaging)
        self._modules['media'] = media
        
        # Initialize avatars module
        logger.info("Initializing avatars module...")
        try:
            from src.core import avatars
            avatars.setup(self.db)
            self._modules['avatars'] = avatars
            logger.info("Avatars module initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize avatars module: {e}")
        
        # Initialize voice module
        logger.info("Initializing voice module...")
        voice.setup(self.db, auth, servers, relationships, presence)
        self._modules['voice'] = voice
        
        # Initialize voice signaling with SFU configuration
        voice_config = config.get("voice") or {}
        if voice_config.get("enabled", False):
            logger.info("Initializing voice signaling module...")
            signaling.setup(
                voice_module=voice,
                events_module=None,  # TODO: Add events module when available
                sfu_backend=voice_config.get("sfu_backend", "mediasoup"),
                mediasoup_url=voice_config.get("mediasoup_url", "https://localhost:4443"),
                janus_url=voice_config.get("janus_url", "http://localhost:8088/janus"),
                stun_urls=voice_config.get("stun_urls", ["stun:stun.l.google.com:19302"]),
                turn_urls=voice_config.get("turn_urls", []),
                turn_secret=voice_config.get("turn_secret", ""),
                turn_ttl=voice_config.get("turn_ttl", 86400),
                turn_username=voice_config.get("turn_username", ""),
                turn_credential=voice_config.get("turn_credential", ""),
            )
            self._modules['signaling'] = signaling
            sfu_url = voice_config.get("mediasoup_url") if voice_config.get("sfu_backend") == "mediasoup" else voice_config.get("janus_url")
            logger.info(f"Voice signaling initialized with {voice_config.get('sfu_backend', 'mediasoup')} backend at {sfu_url}")
            if voice_config.get("turn_urls"):
                logger.info(f"TURN servers configured: {len(voice_config.get('turn_urls', []))} servers")
            if voice_config.get("log_connections", False):
                logger.info("Voice connection logging enabled")
        else:
            logger.info("Voice module disabled in configuration")
        
        # Initialize telemetry module
        telemetry_config = config.get("telemetry") or {}
        if telemetry_config.get("enabled", True):
            logger.info("Initializing telemetry module...")
            try:
                from src.core import telemetry
                telemetry.setup(self.db)
                self._modules['telemetry'] = telemetry
                logger.info("Telemetry module initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize telemetry module: {e}")
        
        # Initialize admin module
        admin_config = config.get("admin_ui") or {}
        if admin_config.get("enabled", True):
            logger.info("Initializing admin module...")
            try:
                from src.core import admin
                admin.setup(self.db, auth)
                self._modules['admin'] = admin
                logger.info(f"Admin module initialized (path: {admin_config.get('path', '/admin')})")
            except Exception as e:
                logger.warning(f"Failed to initialize admin module: {e}")
        
        return auth, messaging, servers, relationships, presence, reactions, embeds, webhooks, settings, media
    
    def create_application(self, auth, messaging, servers, relationships, presence, reactions, embeds, webhooks, settings, media):
        """Create and configure the FastAPI application."""
        from src.api import setup as api_setup, create_app
        
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
            organizations_module=self._modules.get('organizations'),
            features_module=self._modules.get('features'),
            avatars_module=self._modules.get('avatars'),
        )
        
        self.app = create_app(enable_rate_limiting=False, enable_docs=True)
        return self.app
    
    async def notify_clients_shutdown(self, message: str = "Server shutting down"):
        """Notify connected WebSocket clients about shutdown."""
        try:
            from src.api import websocket
            if websocket.is_setup():
                await websocket.broadcast_server_status({
                    "state": "shutting_down",
                    "message": message,
                    "restart_at": None
                })
                logger.info("Notified clients of shutdown")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")
    
    async def notify_clients_restart(self, estimated_seconds: int = 10):
        """Notify connected WebSocket clients about restart."""
        try:
            from src.api import websocket
            if websocket.is_setup():
                await websocket.broadcast_server_status({
                    "state": "restarting",
                    "message": "Server is restarting",
                    "estimated_downtime_seconds": estimated_seconds
                })
                logger.info("Notified clients of restart")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")
    
    def cleanup(self):
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

    def run(self, host: str = None, port: int = None):
        """Run the server with graceful shutdown support."""
        import uvicorn
        
        server_config = config.get("server", {})
        host = host or server_config.get("host", "0.0.0.0")
        port = port or server_config.get("port", 8000)
        
        # Check TLS configuration
        ssl_config = {}
        tls_config = config.get("tls", {})
        use_https = False
        
        if tls_config.get("enabled", False) or tls_config.get("auto_generate_self_signed", False):
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
        uvi_config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="info",
            loop="asyncio",
            **ssl_config
        )
        
        self.server = uvicorn.Server(uvi_config)
        
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
                asyncio.run_coroutine_threadsafe(
                    self.notify_clients_shutdown(),
                    loop
                )
            
            self.server.should_exit = True
        
        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        
        # SIGTERM is not available on Windows, only register if supported
        if hasattr(signal, 'SIGTERM'):
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
    
    def request_restart(self):
        """Request a server restart."""
        self.restart_requested = True
        self.shutdown_event.set()
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.notify_clients_restart())
        except Exception:
            pass
        
        if self.server:
            self.server.should_exit = True


def main():
    """Main entry point for the PlexiChat server."""
    server = PlexiChatServer()
    
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
    config_path = server.setup_config()
    early_logs.append(("debug", f"Config loaded from: {config_path}"))
    
    # Setup logging - NOW we can use logger
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
    
    # Setup utilities
    server.setup_utilities()
    
    # Initialize modules
    modules = server.initialize_modules()
    
    # Create application
    server.create_application(*modules)
    
    # Get server settings
    host = os.getenv("HOST", config.get("server", {}).get("host", "0.0.0.0"))
    port = int(os.getenv("PORT", config.get("server", {}).get("port", 8000)))
    
    # Run server (returns True if restart was requested)
    should_restart = server.run(host, port)
    
    if should_restart:
        logger.info("Restart requested, restarting server...")
        # Re-execute the script
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    logger.info("Server shutdown complete")


def _check_security_keys():
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
    
    # Check messaging encryption key
    messaging_config = config.get("messaging", {})
    encryption_key = messaging_config.get("encryption_key", "")
    default_keys = ["", "CHANGE_ME_DEFAULT_ENCRYPTION_KEY_32B", "changeme", "change-me"]
    if encryption_key in default_keys:
        # Note: If no key is configured, the system auto-generates one in ~/.plexichat/data/.encryption_key
        # This is secure but the warning helps users know to back up this key
        warnings.append("messaging.encryption_key is using auto-generated key (back up ~/.plexichat/data/.encryption_key)")
    
    # Log warnings
    if warnings:
        logger.warning("=" * 60)
        logger.warning("SECURITY WARNING: Default/placeholder keys detected!")
        logger.warning("=" * 60)
        for warning in warnings:
            logger.warning(f"  - {warning}")
        logger.warning("Please update these values in your config file for production use.")
        logger.warning("=" * 60)


if __name__ == "__main__":
    main()
