"""
Database schema for messaging module.

Tables are auto-created on messaging.setup() if they don't exist.
All IDs use Snowflake format for distributed generation.
"""

SCHEMA_SQLITE = """
-- Conversations table
CREATE TABLE IF NOT EXISTS msg_conversations (
    id INTEGER PRIMARY KEY,
    conversation_type TEXT NOT NULL,
    name TEXT,
    owner_id INTEGER,
    max_participants INTEGER NOT NULL DEFAULT 100,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_message_id INTEGER,
    last_message_at INTEGER,
    encrypted INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    deleted_at INTEGER,
    metadata TEXT,
    FOREIGN KEY (owner_id) REFERENCES auth_users(id) ON DELETE SET NULL
);

-- Participants table
CREATE TABLE IF NOT EXISTS msg_participants (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at INTEGER NOT NULL,
    last_read_message_id INTEGER,
    last_read_at INTEGER,
    muted INTEGER DEFAULT 0,
    muted_until INTEGER,
    permissions TEXT,
    nickname TEXT,
    FOREIGN KEY (conversation_id) REFERENCES msg_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(conversation_id, user_id)
);

-- Messages table
CREATE TABLE IF NOT EXISTS msg_messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_encrypted TEXT,
    message_type TEXT NOT NULL DEFAULT 'text',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    edited INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    deleted_at INTEGER,
    reply_to_id INTEGER,
    webhook_id INTEGER,
    metadata TEXT,
    FOREIGN KEY (conversation_id) REFERENCES msg_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (author_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (reply_to_id) REFERENCES msg_messages(id) ON DELETE SET NULL
);

-- Message status (delivery/read receipts)
CREATE TABLE IF NOT EXISTS msg_message_status (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    FOREIGN KEY (message_id) REFERENCES msg_messages(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(message_id, user_id)
);

-- Pinned messages
CREATE TABLE IF NOT EXISTS msg_pinned (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    pinned_by INTEGER NOT NULL,
    pinned_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES msg_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (message_id) REFERENCES msg_messages(id) ON DELETE CASCADE,
    FOREIGN KEY (pinned_by) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(conversation_id, message_id)
);

-- Attachments
CREATE TABLE IF NOT EXISTS msg_attachments (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    url TEXT NOT NULL,
    url_encrypted TEXT,
    created_at INTEGER NOT NULL,
    metadata TEXT,
    deleted INTEGER DEFAULT 0,
    FOREIGN KEY (message_id) REFERENCES msg_messages(id) ON DELETE CASCADE
);

-- User content filter settings
CREATE TABLE IF NOT EXISTS msg_content_filters (
    user_id INTEGER PRIMARY KEY,
    profanity_filter INTEGER DEFAULT 0,
    nsfw_filter INTEGER DEFAULT 0,
    spoiler_click_to_reveal INTEGER DEFAULT 1,
    custom_blocked_words TEXT,
    filter_action TEXT DEFAULT 'censor',
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- User message settings
CREATE TABLE IF NOT EXISTS msg_user_settings (
    user_id INTEGER PRIMARY KEY,
    allow_dms_from TEXT DEFAULT 'everyone',
    auto_create_dms INTEGER DEFAULT 1,
    max_message_length INTEGER,
    max_attachment_size INTEGER,
    max_attachments_per_message INTEGER,
    read_receipts_enabled INTEGER DEFAULT 1,
    typing_indicators_enabled INTEGER DEFAULT 1,
    compact_messages_enabled INTEGER DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- DM lookup table for quick existing DM checks
CREATE TABLE IF NOT EXISTS msg_dm_lookup (
    id INTEGER PRIMARY KEY,
    user1_id INTEGER NOT NULL,
    user2_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES msg_conversations(id) ON DELETE CASCADE,
    UNIQUE(user1_id, user2_id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_msg_conversations_owner ON msg_conversations(owner_id);
CREATE INDEX IF NOT EXISTS idx_msg_conversations_updated ON msg_conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_msg_conversations_type ON msg_conversations(conversation_type);

CREATE INDEX IF NOT EXISTS idx_msg_participants_conv ON msg_participants(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_participants_user ON msg_participants(user_id);
CREATE INDEX IF NOT EXISTS idx_msg_participants_conv_user ON msg_participants(conversation_id, user_id);

CREATE INDEX IF NOT EXISTS idx_msg_messages_conv ON msg_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_messages_author ON msg_messages(author_id);
CREATE INDEX IF NOT EXISTS idx_msg_messages_conv_created ON msg_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_messages_reply ON msg_messages(reply_to_id);

CREATE INDEX IF NOT EXISTS idx_msg_status_message ON msg_message_status(message_id);
CREATE INDEX IF NOT EXISTS idx_msg_status_user ON msg_message_status(user_id);

CREATE INDEX IF NOT EXISTS idx_msg_pinned_conv ON msg_pinned(conversation_id);

CREATE INDEX IF NOT EXISTS idx_msg_attachments_message ON msg_attachments(message_id);

CREATE INDEX IF NOT EXISTS idx_msg_dm_lookup_users ON msg_dm_lookup(user1_id, user2_id);

-- Additional performance indexes for read receipts and unread counts
CREATE INDEX IF NOT EXISTS idx_msg_messages_conv_author ON msg_messages(conversation_id, author_id);
CREATE INDEX IF NOT EXISTS idx_msg_messages_conv_id_deleted ON msg_messages(conversation_id, id, deleted);
CREATE INDEX IF NOT EXISTS idx_msg_participants_last_read ON msg_participants(conversation_id, user_id, last_read_message_id);
"""

SCHEMA_STATEMENTS = [stmt.strip() for stmt in SCHEMA_SQLITE.split(";") if stmt.strip()]


def create_tables(db) -> None:
    """
    Create all messaging tables if they don't exist.

    Args:
        db: Database instance (must be connected)
    """
    for statement in SCHEMA_STATEMENTS:
        if statement:
            converted = (
                db.convert_schema(statement)
                if hasattr(db, "convert_schema")
                else statement
            )
            db.execute(converted)

    # Run migrations for columns added after initial schema
    _run_migrations(db)


def _run_migrations(db) -> None:
    """
    Run schema migrations for columns/indexes added after initial release.
    These are safe to run multiple times (idempotent).
    """
    # Migration: Add webhook_id column to msg_messages (added for webhook support)
    try:
        # Check if column exists first
        db_type = getattr(db, "db_type", getattr(db, "type", "sqlite"))
        column_exists = False

        if db_type == "postgres":
            result = db.fetch_one(
                """SELECT column_name FROM information_schema.columns 
                   WHERE table_name = 'msg_messages' AND column_name = 'webhook_id'"""
            )
            column_exists = result is not None
        else:
            # SQLite
            rows = db.fetch_all("PRAGMA table_info(msg_messages)")
            column_exists = any(row["name"] == "webhook_id" for row in rows)

        if not column_exists:
            db.execute("ALTER TABLE msg_messages ADD COLUMN webhook_id INTEGER")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_messages_webhook ON msg_messages(webhook_id)"
            )
        else:
            # Ensure index exists even if column was already there
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_messages_webhook ON msg_messages(webhook_id)"
            )
    except Exception as e:
        import utils.logger as logger

        logger.debug(f"Migration webhook_id: {e}")

    # Migration: Add compact_messages_enabled column to msg_user_settings
    try:
        db_type = getattr(db, "db_type", getattr(db, "type", "sqlite"))
        column_exists = False

        if db_type == "postgres":
            result = db.fetch_one(
                """SELECT column_name FROM information_schema.columns 
                   WHERE table_name = 'msg_user_settings' AND column_name = 'compact_messages_enabled'"""
            )
            column_exists = result is not None
        else:
            # SQLite
            rows = db.fetch_all("PRAGMA table_info(msg_user_settings)")
            column_exists = any(row["name"] == "compact_messages_enabled" for row in rows)

        if not column_exists:
            db.execute("ALTER TABLE msg_user_settings ADD COLUMN compact_messages_enabled INTEGER DEFAULT 1")
            logger.info("Migrated msg_user_settings: added compact_messages_enabled column")
    except Exception as e:
        import utils.logger as logger
        logger.debug(f"Migration compact_messages_enabled: {e}")


def drop_tables(db) -> None:
    """
    Drop all messaging tables. USE WITH CAUTION.

    Args:
        db: Database instance (must be connected)
    """
    tables = [
        "msg_dm_lookup",
        "msg_user_settings",
        "msg_content_filters",
        "msg_attachments",
        "msg_pinned",
        "msg_message_status",
        "msg_messages",
        "msg_participants",
        "msg_conversations",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
