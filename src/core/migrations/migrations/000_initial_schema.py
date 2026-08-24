"""
Initial schema migration.

Creates all core tables using module schema definitions.
This is the foundational migration that sets up the entire database schema.

All schema changes currently belong in the module schema definitions imported
below. Keep this initial migration authoritative for new installations; the
001 migration is reserved for migration-system self-tests.
"""

import logging
import os

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
from src.core.media.schema import (  # noqa: E402
    create_reference_guards as create_media_reference_guards,
    create_tables as create_media_tables,
)
from src.core.media.deduplication import create_tables as create_media_dedup_tables  # noqa: E402
from src.core.media.chunked import create_tables as create_media_chunked_tables  # noqa: E402
from src.core.reports.schema import create_tables as create_reports_tables  # noqa: E402
from src.core.feedback import create_tables as create_feedback_tables  # noqa: E402
from src.core.avatars import create_tables as create_avatars_tables  # noqa: E402
from src.core.telemetry import create_tables as create_telemetry_tables  # noqa: E402
from src.core.search.schema import create_tables as create_search_tables  # noqa: E402
from src.core.automod.schema import create_tables as create_automod_tables  # noqa: E402
from src.core.plexijoin.schema import create_tables as create_plexijoin_tables  # noqa: E402
from src.core.dsar.schema import create_tables as create_dsar_tables  # noqa: E402
from src.core.artifacts.schema import create_tables as create_artifacts_tables  # noqa: E402
from src.core.admin.schema import create_admin_tables  # noqa: E402
from src.core.dmspam.schema import create_dmspam_tables  # noqa: E402
from src.core.suite.stash.schema import create_tables as create_stash_tables  # noqa: E402
from src.core.suite.sum.schema import create_tables as create_sum_tables  # noqa: E402
from src.core.chat_tracking.schema import create_chat_tracking_tables  # noqa: E402


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
        ("plexijoin", create_plexijoin_tables),
        ("dsar", create_dsar_tables),
        ("artifacts", create_artifacts_tables),
        ("admin", create_admin_tables),
        ("dmspam", create_dmspam_tables),
        ("chat_tracking", create_chat_tracking_tables),
        # Suite schema ownership stays in the domain modules. Migration 000
        # only orchestrates their idempotent creators; workbook cells remain
        # exclusively inside independent .plexisum SQLite files.
        ("suite_stash", create_stash_tables),
        ("suite_sum_metadata", create_sum_tables),
        # Reference guards must be installed after every dependent domain
        # table exists. The guard definitions remain owned by media/schema.py;
        # migration 000 only orchestrates their creation.
        ("media_reference_guards", create_media_reference_guards),
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


# Explicit allowlist of application-schema tables created by this migration's
# up(). Rollback may ONLY ever drop tables in this list. Migration-tracking
# tables (migrations_history, migration_logs, migration_uptime,
# emergency_override_tokens, migration_lock) and any other non-app table are
# deliberately excluded and will never be dropped by down().
ROLLBACK_TABLE_ALLOWLIST = frozenset(
    {
        "admin_approval_comments",
        "admin_approvals",
        "admin_audit_log",
        "admin_notes",
        "admin_notes_versioning",
        "admin_role_assignments",
        "admin_roles",
        "admin_sessions",
        "admin_users",
        "app_applications",
        "app_approved_bots",
        "app_bot_profiles",
        "app_bot_requests",
        "app_commands",
        "app_installations",
        "app_interactions",
        "app_oauth_codes",
        "app_oauth_tokens",
        "app_webhook_deliveries",
        "application_settings",
        "artifact_ops",
        "artifacts",
        "auth_2fa_challenges",
        "auth_api_access_token_events",
        "auth_api_access_token_scopes",
        "auth_api_access_tokens",
        "auth_audit_log",
        "auth_bots",
        "auth_deletion_records",
        "auth_devices",
        "auth_email_tokens",
        "auth_external_accounts",
        "auth_failed_logins",
        "auth_internal_secrets",
        "auth_ip_blacklist",
        "auth_known_ips",
        "auth_passkey_challenges",
        "auth_passkeys",
        "auth_sessions",
        "auth_user_notes",
        "auth_users",
        "auth_ws_tickets",
        "automod_audit",
        "automod_exemptions",
        "automod_rate_tracking",
        "automod_reputation",
        "automod_rules",
        "automod_violations",
        "channel_ratchet_intervals",
        "dm_spam_events",
        "dm_spam_filters",
        "dsar_export_manifest",
        "dsar_requests",
        "embed_embeds",
        "embed_fields",
        "embed_message_embeds",
        "embed_preview_cache",
        "embed_preview_rate_limits",
        "feedback",
        "media_blocked_hashes",
        "media_blocked_users",
        "media_file_deletion_locks",
        "media_file_deletion_tombstones",
        "media_file_hashes",
        "media_files",
        "media_hash_reports",
        "media_proxy_cache",
        "media_thumbnails",
        "media_upload_sessions",
        "message_reports",
        "msg_attachments",
        "msg_content_filters",
        "msg_conversations",
        "msg_dm_lookup",
        "msg_edit_history",
        "msg_forwarded",
        "msg_message_status",
        "msg_message_tokens",
        "msg_messages",
        "msg_participants",
        "msg_pinned",
        "msg_scheduled",
        "msg_user_settings",
        "notif_channel_overrides",
        "notif_notifications",
        "notif_settings",
        "notif_unread",
        "plexijoin_connections",
        "plexijoin_inbound_requests",
        "plexijoin_traffic_log",
        "poll_options",
        "poll_polls",
        "poll_voters",
        "poll_votes",
        "pres_activity",
        "pres_custom_status",
        "pres_presence",
        "pres_typing",
        "push_tokens",
        "react_custom_emoji",
        "react_reactions",
        "rel_blocked",
        "rel_friend_requests",
        "rel_friends",
        "reports",
        "saved_searches",
        "search_bump_history",
        "search_categories",
        "search_history",
        "search_message_index",
        "search_messages_fts",
        "search_messages_fts_config",
        "search_messages_fts_content",
        "search_messages_fts_data",
        "search_messages_fts_docsize",
        "search_messages_fts_idx",
        "search_server_index",
        "search_server_listings",
        "search_servers_fts",
        "search_servers_fts_config",
        "search_servers_fts_content",
        "search_servers_fts_data",
        "search_servers_fts_docsize",
        "search_servers_fts_idx",
        "search_user_index",
        "search_users_fts",
        "search_users_fts_config",
        "search_users_fts_content",
        "search_users_fts_data",
        "search_users_fts_docsize",
        "search_users_fts_idx",
        "server_artifact_feature_settings",
        "server_artifact_settings",
        "server_icons",
        "soundboard_permissions",
        "soundboard_sounds",
        "soundboard_usage",
        "soundboard_user_cooldowns",
        "srv_audit_log",
        "srv_bans",
        "srv_categories",
        "srv_channel_overrides",
        "srv_channels",
        "srv_event_rsvps",
        "srv_invites",
        "srv_member_roles",
        "srv_members",
        "srv_onboarding_progress",
        "srv_onboarding_steps",
        "srv_roles",
        "srv_scheduled_events",
        "srv_servers",
        "srv_template_data",
        "srv_templates",
        "srv_welcome_screens",
        "stash_collection_grants",
        "stash_collections",
        "stash_download_tickets",
        "stash_grants",
        "stash_item_text",
        "stash_items",
        "stash_share_links",
        "stash_versions",
        "sticker_packs",
        "sticker_stickers",
        "sticker_usage",
        "suite_audit_log",
        "sum_features",
        "sum_grants",
        "sum_operations",
        "sum_range_permissions",
        "sum_revision_blobs",
        "sum_snapshots",
        "sum_workbooks",
        "system_alerts",
        "telemetry_response_times",
        "thread_members",
        "thread_messages",
        "thread_threads",
        "user_avatars",
        "user_bookmarks",
        "user_feature_usage",
        "user_features",
        "user_features_audit",
        "user_last_chat",
        "user_profiles",
        "user_recent_chats",
        "user_reports",
        "user_settings",
        "username_blacklist",
        "voice_calls",
        "voice_channel_settings",
        "voice_server_settings",
        "voice_speaker_requests",
        "voice_stage_instances",
        "voice_states",
        "webhook_messages",
        "webhook_retry_queue",
        "webhook_webhooks",
    }
)

# Environment variable that must be explicitly set to "1" before migration
# 000's rollback is allowed to run at all. Without it, down() refuses.
DESTRUCTIVE_ROLLBACK_ENV_VAR = "MIGRATIONS_ALLOW_DESTRUCTIVE_ROLLBACK"


def _list_existing_tables(db) -> list:
    """List user tables present in the database, dialect-aware."""
    if db.type == "sqlite":
        rows = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [
            row["name"] if isinstance(row, dict) else row[0] for row in (rows or [])
        ]
    rows = db.fetch_all(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    return [
        row["table_name"] if isinstance(row, dict) else row[0] for row in (rows or [])
    ]


def _order_children_first(db, tables):
    """Order tables so referencing (child) tables are dropped before the
    tables they reference. PostgreSQL gets CASCADE anyway; this matters for
    SQLite where dropping a parent while FK enforcement is active fails when
    a still-present child references it."""
    selected = set(tables)
    refs_of = {}
    for table in tables:
        refs = set()
        try:
            rows = db.fetch_all(f'PRAGMA foreign_key_list("{table}")') or []
            for row in rows:
                ref = row["table"] if isinstance(row, dict) else row[2]
                if ref in selected and ref != table:
                    refs.add(ref)
        except Exception:
            pass
        refs_of[table] = refs

    ordered = []
    remaining = set(tables)
    while remaining:
        # A table is droppable once nothing left undropped references it.
        ready = sorted(
            t
            for t in remaining
            if not any(t in refs_of[r] for r in remaining if r != t)
        )
        if not ready:
            # Dependency cycle: emit the rest in stable order.
            ordered.extend(sorted(remaining))
            break
        ordered.extend(ready)
        remaining -= set(ready)
    return ordered


def down(db):
    """Rollback the initial schema migration.

    DESTRUCTIVE but GUARDED: only tables in ROLLBACK_TABLE_ALLOWLIST are
    dropped; migration-history/tracking tables and any unknown (non-app)
    tables are never touched. The operation is refused outright unless the
    environment variable MIGRATIONS_ALLOW_DESTRUCTIVE_ROLLBACK=1 is set.

    Drop statements are dialect-aware: SQLite does not support
    ``DROP TABLE ... CASCADE`` (it is a syntax error), so CASCADE is only
    emitted on PostgreSQL.
    """
    if os.environ.get(DESTRUCTIVE_ROLLBACK_ENV_VAR, "") != "1":
        logger.error(
            "Migration 000 rollback REFUSED: this drops all application tables. "
            f"To permit it, set {DESTRUCTIVE_ROLLBACK_ENV_VAR}=1 in the "
            "environment and re-run."
        )
        raise RuntimeError(
            "Migration 000 rollback refused: destructive rollback requires "
            f"{DESTRUCTIVE_ROLLBACK_ENV_VAR}=1"
        )

    logger.warning(
        "Migration 000 rollback: dropping allow-listed app tables - DESTRUCTIVE"
    )

    table_names = _list_existing_tables(db)

    targets = [t for t in table_names if t in ROLLBACK_TABLE_ALLOWLIST]
    protected = sorted(t for t in table_names if t not in ROLLBACK_TABLE_ALLOWLIST)
    if db.type == "sqlite":
        targets = _order_children_first(db, targets)
    else:
        targets = sorted(targets)
    if protected:
        logger.warning(
            "Migration 000 rollback: NOT dropping %d non-allow-listed table(s): %s",
            len(protected),
            ", ".join(protected),
        )

    cascade = " CASCADE" if db.type == "postgres" else ""
    dropped = []
    failures = []
    for table in targets:
        try:
            db.execute(f'DROP TABLE IF EXISTS "{table}"{cascade}')
            dropped.append(table)
        except Exception as e:
            # Record honestly instead of swallowing - a partially failed drop
            # must not be reported as a successful rollback.
            failures.append((table, str(e)))
            logger.error(f"Migration 000 rollback: could not drop table {table}: {e}")

    logger.warning(
        "Migration 000 rollback complete: dropped=%d skipped(protected/unknown)=%d failed=%d",
        len(dropped),
        len(protected),
        len(failures),
    )
    if failures:
        raise RuntimeError(
            "Migration 000 rollback partially failed; undropped tables: "
            + ", ".join(t for t, _ in failures)
        )
