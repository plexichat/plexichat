"""
Initial schema migration.

Creates all core tables using module schema definitions.
"""

from src.core.auth.schema import create_tables as create_auth_tables
from src.core.servers.schema import create_tables as create_servers_tables
from src.core.messaging.schema import create_tables as create_messaging_tables
from src.core.relationships.schema import create_tables as create_relationships_tables
from src.core.reactions.schema import create_tables as create_reactions_tables
from src.core.webhooks.schema import create_tables as create_webhooks_tables
from src.core.embeds.schema import create_tables as create_embeds_tables
from src.core.notifications.schema import create_tables as create_notifications_tables
from src.core.threads.schema import create_tables as create_threads_tables
from src.core.presence.schema import create_tables as create_presence_tables
from src.core.settings.schema import create_tables as create_settings_tables
from src.core.features.schema import create_tables as create_features_tables
from src.core.polls.schema import create_tables as create_polls_tables
from src.core.voice.schema import create_tables as create_voice_tables
from src.core.applications.schema import create_tables as create_applications_tables
from src.core.stickers.schema import create_tables as create_stickers_tables
from src.core.soundboard.schema import create_tables as create_soundboard_tables
from src.core.media.schema import create_tables as create_media_tables
from src.core.media.deduplication import create_tables as create_media_dedup_tables
from src.core.media.chunked import create_tables as create_media_chunked_tables
from src.core.reports import create_tables as create_reports_tables
from src.core.feedback import create_tables as create_feedback_tables
from src.core.avatars import create_tables as create_avatars_tables
from src.core.telemetry import create_tables as create_telemetry_tables
from src.core.search.schema import create_tables as create_search_tables
from src.core.automod.schema import create_tables as create_automod_tables


def up(db):
    create_auth_tables(db)
    create_servers_tables(db)
    create_messaging_tables(db)
    create_relationships_tables(db)
    create_reactions_tables(db)
    create_webhooks_tables(db)
    create_embeds_tables(db)
    create_notifications_tables(db)
    create_threads_tables(db)
    create_presence_tables(db)
    create_settings_tables(db)
    create_features_tables(db)
    create_polls_tables(db)
    create_voice_tables(db)
    create_applications_tables(db)
    create_stickers_tables(db)
    create_soundboard_tables(db)
    create_media_tables(db)
    create_media_dedup_tables(db)
    create_media_chunked_tables(db)
    create_reports_tables(db)
    create_feedback_tables(db)
    create_avatars_tables(db)
    create_telemetry_tables(db)
    create_search_tables(db)
    create_automod_tables(db)


def down(db):
    pass
