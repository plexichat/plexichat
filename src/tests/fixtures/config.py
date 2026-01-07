"""
Shared test configuration.

Single source of truth for all test configuration values.
"""

# Standard test password that meets all requirements
TEST_PASSWORD = "TestPass123!"
TEST_VERSION = "a.1.0-1"


def get_test_config():
    """
    Get the unified test configuration.

    This replaces the 15+ duplicate get_test_config() functions
    that were scattered across conftest.py files.
    """
    return {
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
            "security": {
                "max_failed_attempts": 3,
                "lockout_duration_minutes": 1,
                "token_verify_rate_limit": 10000,
            },
            "totp": {
                "enabled": True,
                "issuer": "TestApp",
                "digits": 6,
                "interval": 30,
                "backup_code_count": 5,
                "backup_code_length": 8,
            },
            "password": {
                "min_length": 8,
                "max_length": 128,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True,
            },
            "bots": {
                "token_bytes": 48,
                "require_owner_2fa": False,
            },
        },
        "messaging": {
            "max_message_length": 4000,
            "max_group_participants": 100,
            "max_attachment_size": 10485760,
            "max_attachments_per_message": 10,
            "dm_auto_create": True,
            "encrypt_messages": False,
            "encrypt_attachments": False,
            "message_preview_length": 100,
        },
        "servers": {
            "max_servers_per_user": 100,
            "max_channels_per_server": 500,
            "max_roles_per_server": 250,
            "server_name_min_length": 2,
            "server_name_max_length": 100,
            "channel_name_min_length": 1,
            "channel_name_max_length": 100,
            "role_name_min_length": 1,
            "role_name_max_length": 100,
            "invite_code_length": 8,
        },
        "webhooks": {
            "max_webhooks_per_channel": 10,
            "max_webhooks_per_server": 50,
            "max_message_length": 2000,
            "max_embeds_per_message": 10,
        },
        "embeds": {
            "max_embeds_per_message": 10,
            "max_title_length": 256,
            "max_description_length": 4096,
            "max_fields": 25,
            "max_field_name_length": 256,
            "max_field_value_length": 1024,
            "max_footer_length": 2048,
            "max_author_name_length": 256,
            "max_total_characters": 6000,
        },
        "presence": {
            "typing_timeout_ms": 10000,
            "timeout_ms": 300000,
        },
        "api": {
            "title": "PlexiChat API Test",
            "description": "REST API for PlexiChat messaging platform",
            "version": TEST_VERSION,
            "api_prefix": "/api/v1",
            "debug": True,
            "cors_origins": ["http://testserver", "http://localhost:3000"],
            "allow_wildcard_cors": True,
            "cors_allow_credentials": True,
            "cors_allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "cors_allow_headers": [
                "Authorization",
                "Content-Type",
                "X-Requested-With",
                "Accept",
                "Origin",
                "X-Custom-Header",
            ],
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        },
        "application": {
            "name": "PlexiChat",
            "version": TEST_VERSION,
            "environment": "test",
        },
        "versioning": {
            "min_supported_version": TEST_VERSION,
            "update_url": None,
        },
        "media": {
            "storage_backend": "local",
            "local_path": "temp_test_session/uploads",
            "local_url": "/media",
            "size_limits": {
                "image": 10485760,
                "video": 104857600,
                "audio": 52428800,
                "document": 26214400,
                "other": 10485760,
            },
            "allowed_types": {
                "image": ["image/jpeg", "image/png", "image/gif", "image/webp"],
                "video": ["video/mp4", "video/webm", "video/quicktime"],
                "audio": ["audio/mpeg", "audio/ogg", "audio/wav"],
                "document": ["application/pdf", "text/plain"],
            },
            "thumbnail_sizes": [64, 128, 256, 512],
            "signing_key": "test-signing-key-for-media",
            "signing_expiry": 3600,
            "scanner_enabled": False,
            "proxy_enabled": False,
            "compression": {"enabled": False},
        },
        "search": {
            "backend": "sqlite_fts5",
            "batch_size": 100,
            "write_time_indexing": True,
            "result_limit": 100,
            "discovery": {
                "min_members_for_listing": 2,
                "bump_cooldown_hours": 0,
                "max_tags": 10,
            },
        },
        "applications": {
            "max_applications_per_user": 1000,
        },
    }
