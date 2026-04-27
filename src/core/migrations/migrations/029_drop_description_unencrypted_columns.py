"""
Drop unencrypted description and topic columns after encryption migration.

MIGRATION_METADATA:
{
    "irreversible": true,
    "depends_on": ["027"],
    "description": "Drops unencrypted description and topic columns after encryption verification period",
    "risk_level": "high",
    "backup_required": true
}

This migration is IRREVERSIBLE. It should only be run after:
1. Migration 027 (migrate_description_encrypted_data) has been applied
2. The encryption feature has been verified to work correctly
3. A sufficient verification period (configurable) has passed
4. Sufficient server uptime has elapsed since migration 027 (configurable delay)

After this migration, the original unencrypted columns are permanently removed.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """
    Apply migration - drop unencrypted columns.

    Drops the following columns:
    - srv_servers.description
    - srv_channels.topic
    - thread_threads.name
    - sticker_packs.description

    These columns are replaced by their _encrypted counterparts.
    """
    logger.info("Migration 028: Dropping unencrypted description and topic columns")

    # Drop from srv_servers
    if db.type == "postgres":
        db.execute("ALTER TABLE srv_servers DROP COLUMN IF EXISTS description")
    else:
        # SQLite requires table recreation - preserve all columns except description
        db.execute("""
            CREATE TABLE srv_servers_new (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                description_encrypted TEXT,
                icon_url TEXT,
                default_role_id INTEGER,
                default_channel_id INTEGER,
                system_channel_id INTEGER,
                verification_level INTEGER DEFAULT 0,
                max_reactions_per_message INTEGER DEFAULT 20,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deleted INTEGER DEFAULT 0,
                deleted_at INTEGER,
                metadata TEXT
            )
        """)
        db.execute("""
            INSERT INTO srv_servers_new
            SELECT id, name, owner_id, description_encrypted, icon_url, default_role_id,
                   default_channel_id, system_channel_id, verification_level,
                   max_reactions_per_message, created_at, updated_at, deleted, deleted_at, metadata
            FROM srv_servers
        """)
        db.execute("DROP TABLE srv_servers")
        db.execute("ALTER TABLE srv_servers_new RENAME TO srv_servers")

    logger.info("Migration 028: Dropped srv_servers.description")

    # Drop from srv_channels
    if db.type == "postgres":
        db.execute("ALTER TABLE srv_channels DROP COLUMN IF EXISTS topic")
    else:
        # SQLite requires table recreation - preserve all columns except topic
        db.execute("""
            CREATE TABLE srv_channels_new (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                channel_type TEXT NOT NULL DEFAULT 'text',
                category_id INTEGER,
                position INTEGER DEFAULT 0,
                topic_encrypted TEXT,
                nsfw INTEGER DEFAULT 0,
                slowmode_seconds INTEGER DEFAULT 0,
                read_receipts_enabled INTEGER DEFAULT 1,
                conversation_id INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deleted INTEGER DEFAULT 0,
                deleted_at INTEGER,
                metadata TEXT
            )
        """)
        db.execute("""
            INSERT INTO srv_channels_new
            SELECT id, server_id, name, channel_type, category_id, position,
                   topic_encrypted, nsfw, slowmode_seconds, read_receipts_enabled,
                   conversation_id, created_at, updated_at, deleted, deleted_at, metadata
            FROM srv_channels
        """)
        db.execute("DROP TABLE srv_channels")
        db.execute("ALTER TABLE srv_channels_new RENAME TO srv_channels")

    logger.info("Migration 028: Dropped srv_channels.topic")

    # Drop from thread_threads
    if db.type == "postgres":
        db.execute("ALTER TABLE thread_threads DROP COLUMN IF EXISTS name")
    else:
        # SQLite requires table recreation - preserve all columns except name
        db.execute("""
            CREATE TABLE thread_threads_new (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                server_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                name_encrypted TEXT,
                thread_type TEXT NOT NULL DEFAULT 'public',
                state TEXT NOT NULL DEFAULT 'active',
                parent_message_id INTEGER,
                auto_archive_duration INTEGER NOT NULL DEFAULT 1440,
                message_count INTEGER DEFAULT 0,
                member_count INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL,
                archived_at INTEGER,
                last_message_at INTEGER,
                locked INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                conversation_id INTEGER
            )
        """)
        db.execute("""
            INSERT INTO thread_threads_new
            SELECT id, channel_id, server_id, owner_id, name_encrypted,
                   thread_type, state, parent_message_id, auto_archive_duration,
                   message_count, member_count, created_at, archived_at,
                   last_message_at, locked, deleted, conversation_id
            FROM thread_threads
        """)
        db.execute("DROP TABLE thread_threads")
        db.execute("ALTER TABLE thread_threads_new RENAME TO thread_threads")

    logger.info("Migration 028: Dropped thread_threads.name")

    # Drop from sticker_packs
    if db.type == "postgres":
        db.execute("ALTER TABLE sticker_packs DROP COLUMN IF EXISTS description")
    else:
        # SQLite requires table recreation - preserve all columns except description
        db.execute("""
            CREATE TABLE sticker_packs_new (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description_encrypted TEXT,
                pack_type TEXT NOT NULL DEFAULT 'server',
                server_id INTEGER,
                created_by INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                is_public INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            INSERT INTO sticker_packs_new
            SELECT id, name, description_encrypted, pack_type, server_id,
                   created_by, created_at, updated_at, is_public
            FROM sticker_packs
        """)
        db.execute("DROP TABLE sticker_packs")
        db.execute("ALTER TABLE sticker_packs_new RENAME TO sticker_packs")

    logger.info("Migration 028: Dropped sticker_packs.description")


def down(db):
    """
    Rollback - NOT SUPPORTED.

    This migration is irreversible. The unencrypted columns have been permanently
    dropped and cannot be recovered without restoring from a backup.

    To rollback:
    1. Restore the database from a backup taken before this migration
    2. Rollback migration 027 (migrate_description_encrypted_data)
    3. Rollback migration 026 (add_description_encrypted_columns)
    """
    logger.error(
        "Migration 028 rollback: NOT SUPPORTED - this migration is irreversible. "
        "Restore from backup to revert."
    )
    raise RuntimeError("Migration 028 is irreversible and cannot be rolled back")
