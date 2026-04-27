"""
Initial schema migration.

Creates all core tables using module schema definitions.
This is the foundational migration that sets up the entire database schema.
"""

import logging

logger = logging.getLogger(__name__)

from src.core.auth.schema import create_tables as create_auth_tables  # noqa: E402
from src.core.servers.schema import create_tables as create_servers_tables  # noqa: E402
from src.core.messaging.schema import create_tables as create_messaging_tables  # noqa: E402
from src.core.relationships.schema import create_tables as create_relationships_tables  # noqa: E402
from src.core.reactions.schema import create_tables as create_reactions_tables  # noqa: E402
from src.core.webhooks.schema import create_tables as create_webhooks_tables  # noqa: E402
from src.core.embeds.schema import create_tables as create_embeds_tables  # noqa: E402
from src.core.notifications.schema import create_tables as create_notifications_tables  # noqa: E402
from src.core.threads.schema import create_tables as create_threads_tables  # noqa: E402
from src.core.presence.schema import create_tables as create_presence_tables  # noqa: E402
from src.core.settings.schema import create_tables as create_settings_tables  # noqa: E402
from src.core.features.schema import create_tables as create_features_tables  # noqa: E402
from src.core.polls.schema import create_tables as create_polls_tables  # noqa: E402
from src.core.voice.schema import create_tables as create_voice_tables  # noqa: E402
from src.core.applications.schema import create_tables as create_applications_tables  # noqa: E402
from src.core.stickers.schema import create_tables as create_stickers_tables  # noqa: E402
from src.core.soundboard.schema import create_tables as create_soundboard_tables  # noqa: E402
from src.core.media.schema import create_tables as create_media_tables  # noqa: E402
from src.core.media.deduplication import create_tables as create_media_dedup_tables  # noqa: E402
from src.core.media.chunked import create_tables as create_media_chunked_tables  # noqa: E402
from src.core.reports import create_tables as create_reports_tables  # noqa: E402
from src.core.feedback import create_tables as create_feedback_tables  # noqa: E402
from src.core.avatars import create_tables as create_avatars_tables  # noqa: E402
from src.core.telemetry import create_tables as create_telemetry_tables  # noqa: E402
from src.core.search.schema import create_tables as create_search_tables  # noqa: E402
from src.core.automod.schema import create_tables as create_automod_tables  # noqa: E402


def up(db):
    """Apply the initial schema migration.

    Creates all core tables using module schema definitions.
    This migration is idempotent - schema files should handle table existence checks.
    """
    logger.info("Migration 000: Starting initial schema creation")

    # Create tables in dependency order
    schema_creators = [
        ("auth", create_auth_tables),
        ("servers", create_servers_tables),
        ("messaging", create_messaging_tables),
        ("relationships", create_relationships_tables),
        ("reactions", create_reactions_tables),
        ("webhooks", create_webhooks_tables),
        ("embeds", create_embeds_tables),
        ("notifications", create_notifications_tables),
        ("threads", create_threads_tables),
        ("presence", create_presence_tables),
        ("settings", create_settings_tables),
        ("features", create_features_tables),
        ("polls", create_polls_tables),
        ("voice", create_voice_tables),
        ("applications", create_applications_tables),
        ("stickers", create_stickers_tables),
        ("soundboard", create_soundboard_tables),
        ("media", create_media_tables),
        ("media_dedup", create_media_dedup_tables),
        ("media_chunked", create_media_chunked_tables),
        ("reports", create_reports_tables),
        ("feedback", create_feedback_tables),
        ("avatars", create_avatars_tables),
        ("telemetry", create_telemetry_tables),
        ("search", create_search_tables),
        ("automod", create_automod_tables),
    ]

    for name, creator in schema_creators:
        try:
            logger.debug(f"Migration 000: Creating {name} tables")
            creator(db)
            logger.debug(f"Migration 000: {name} tables created successfully")
        except Exception as e:
            logger.error(f"Migration 000: Failed to create {name} tables: {e}")
            raise

    logger.info("Migration 000: Initial schema creation complete")


def down(db):
    """Rollback the initial schema migration.

    WARNING: This is destructive and will drop all tables.
    This should only be used in development/testing environments.
    """
    logger.warning(
        "Migration 000 rollback: DROPPING ALL TABLES - DESTRUCTIVE OPERATION"
    )

    # Get all table names
    if db.type == "sqlite":
        tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        table_names = [row["name"] for row in tables]
    else:
        tables = db.fetch_all(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        table_names = [row["table_name"] for row in tables]

    # Drop tables in reverse dependency order (approximate)
    # Note: This is a simple approach - proper dependency-aware dropping would require more logic
    for table in table_names:
        try:
            db.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            logger.debug(f"Migration 000 rollback: Dropped table {table}")
        except Exception as e:
            logger.warning(f"Migration 000 rollback: Could not drop table {table}: {e}")

    logger.warning("Migration 000 rollback: All tables dropped")
