"""
Database schema for messaging module.

Tables are auto-created on messaging.setup() if they don't exist.
All IDs use Snowflake format for distributed generation.
"""

import logging

from typing import Optional

from src.core.database.core.schema_splitter import split_sql_statements

logger = logging.getLogger(__name__)

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
-- content: always populated, may be plain text OR an ENC:v1:... blob, content_encrypted
-- is a legacy optional mirror, prefer content. content_index is a blind index for search.
CREATE TABLE IF NOT EXISTS msg_messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_encrypted TEXT,
    content_index TEXT,
    message_type TEXT NOT NULL DEFAULT 'text',
    -- Voice message metadata (019)
    voice_duration_ms INTEGER,
    voice_waveform TEXT,
    -- Compact mode + integrity (005)
    compact_messages_enabled INTEGER DEFAULT 1,
    checksum TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    edited INTEGER DEFAULT 0,
    -- Real wall-clock time of the last user edit (NULL = never edited).
    -- Consumers previously fell back to updated_at, which also moves on
    -- tombstones/pin changes and is NOT an edit timestamp.
    edited_at INTEGER,
    deleted INTEGER DEFAULT 0,
    deleted_at INTEGER,
    reply_to_id INTEGER,
    webhook_id INTEGER,
    ratchet_interval_id BIGINT,
    metadata TEXT,
    FOREIGN KEY (conversation_id) REFERENCES msg_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (author_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (reply_to_id) REFERENCES msg_messages(id) ON DELETE SET NULL
);

-- Message search tokens
-- Each whitespace-delimited word of a message's plaintext is blind-indexed
-- here at send/edit time so server-side substring search keeps working even
-- when ``content`` holds an ENC: envelope (the whole-content ``content_index``
-- only supports exact equality). token_index values are keyed hashes; the
-- plaintext is never stored, preserving the search-privacy contract.
CREATE TABLE IF NOT EXISTS msg_message_tokens (
    message_id INTEGER NOT NULL,
    token_index TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (message_id, position),
    FOREIGN KEY (message_id) REFERENCES msg_messages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_msg_message_tokens_token ON msg_message_tokens(token_index);

-- Forwarded messages (019)
CREATE TABLE IF NOT EXISTS msg_forwarded (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    original_message_id INTEGER NOT NULL,
    original_conversation_id INTEGER,
    original_author_id INTEGER,
    forwarded_by INTEGER NOT NULL,
    original_content TEXT,
    original_created_at INTEGER,
    created_at INTEGER NOT NULL
);

-- Scheduled messages (019)
CREATE TABLE IF NOT EXISTS msg_scheduled (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    scheduled_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message_id INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    attachments TEXT
);

-- User bookmarks (019)
CREATE TABLE IF NOT EXISTS user_bookmarks (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    label TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, message_id)
);

-- User profiles (019)
CREATE TABLE IF NOT EXISTS user_profiles (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    bio TEXT,
    banner_url TEXT,
    social_links TEXT,
    custom_status_text TEXT,
    custom_status_emoji TEXT,
    custom_status_expires_at INTEGER,
    pronouns TEXT,
    location TEXT,
    timezone TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
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
    checksum TEXT,
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

-- Message edit history
CREATE TABLE IF NOT EXISTS msg_edit_history (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    editor_id INTEGER NOT NULL,
    old_content TEXT NOT NULL,
    old_content_encrypted TEXT,
    edit_timestamp INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    FOREIGN KEY (message_id) REFERENCES msg_messages(id) ON DELETE CASCADE,
    FOREIGN KEY (editor_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Channel ratchet intervals (045)
CREATE TABLE IF NOT EXISTS channel_ratchet_intervals (
    interval_id BIGINT PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    start_message_id BIGINT NOT NULL,
    end_message_id BIGINT,
    start_key_wrapped TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    last_message_at BIGINT NOT NULL
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

CREATE INDEX IF NOT EXISTS idx_msg_edit_history_message ON msg_edit_history(message_id);
CREATE INDEX IF NOT EXISTS idx_msg_edit_history_editor ON msg_edit_history(editor_id);

CREATE INDEX IF NOT EXISTS idx_msg_dm_lookup_users ON msg_dm_lookup(user1_id, user2_id);

CREATE INDEX IF NOT EXISTS idx_channel_ratchet_intervals_conversation
    ON channel_ratchet_intervals(conversation_id, start_message_id);
CREATE INDEX IF NOT EXISTS idx_channel_ratchet_intervals_open
    ON channel_ratchet_intervals(conversation_id, end_message_id);

-- Additional performance indexes for read receipts and unread counts
CREATE INDEX IF NOT EXISTS idx_msg_messages_conv_author ON msg_messages(conversation_id, author_id);
CREATE INDEX IF NOT EXISTS idx_msg_messages_conv_id_deleted ON msg_messages(conversation_id, id, deleted);
CREATE INDEX IF NOT EXISTS idx_msg_messages_content_index ON msg_messages(content_index);
CREATE INDEX IF NOT EXISTS idx_msg_participants_last_read ON msg_participants(conversation_id, user_id, last_read_message_id);

-- Indexes for msg_forwarded, msg_scheduled, user_bookmarks, user_profiles (019)
CREATE INDEX IF NOT EXISTS idx_msg_fwd_message ON msg_forwarded(message_id);
CREATE INDEX IF NOT EXISTS idx_msg_fwd_original ON msg_forwarded(original_message_id);
CREATE INDEX IF NOT EXISTS idx_msg_fwd_by ON msg_forwarded(forwarded_by);
CREATE INDEX IF NOT EXISTS idx_msg_sched_conv ON msg_scheduled(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_sched_author ON msg_scheduled(author_id);
CREATE INDEX IF NOT EXISTS idx_msg_sched_at ON msg_scheduled(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_msg_sched_status ON msg_scheduled(status);
CREATE INDEX IF NOT EXISTS idx_bookmark_user ON user_bookmarks(user_id);
CREATE INDEX IF NOT EXISTS idx_bookmark_message ON user_bookmarks(message_id);
CREATE INDEX IF NOT EXISTS idx_bookmark_conv ON user_bookmarks(conversation_id);
CREATE INDEX IF NOT EXISTS idx_profile_user ON user_profiles(user_id);
"""

SCHEMA_STATEMENTS = split_sql_statements(SCHEMA_SQLITE)


def _column_exists(db, table: str, column: str) -> Optional[bool]:
    """Best-effort column probe.

    Returns True/False when the backend answers, or None when the check
    cannot be performed (unknown dialect) so callers never misreport.

    Postgres has no ``PRAGMA``, so the SQLite probe raised there and this
    returned None -- which made the caller issue an ALTER for a column that
    already existed. On Postgres that aborted the migration transaction and
    rolled back every table created before it, so migration 000 died with
    "relation auth_users does not exist" (messaging creates tables that
    reference ``auth_users``).
    """
    checker = getattr(db, "column_exists", None)
    if callable(checker):
        try:
            return bool(checker(table, column))
        except Exception:
            return None
    try:
        if getattr(db, "type", None) == "postgres":
            rows = db.fetch_all(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ?",
                (table,),
            )
        else:
            rows = db.fetch_all(f"PRAGMA table_info({table})")
    except Exception:
        return None
    try:
        for row in rows or []:
            name = row["name"] if not isinstance(row, dict) else row.get("name")
            if name == column:
                return True
    except Exception:
        return None
    return False


def _ensure_runtime_columns(db) -> None:
    """Add columns introduced after the initial schema to existing tables.

    CREATE TABLE IF NOT EXISTS never upgrades pre-existing databases, so
    new columns are back-filled here with a guarded ALTER TABLE. A failure
    is logged and the column's presence is verified via PRAGMA table_info;
    if verification says the column is still missing the ALTER is retried
    once before giving up loudly (a missing runtime column silently breaks
    every query that selects it).
    """
    runtime_columns = [
        ("msg_messages", "edited_at", "INTEGER"),
    ]
    for table, column, definition in runtime_columns:
        exists = _column_exists(db, table, column)
        if exists:
            continue

        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except Exception as e:
                logger.warning(
                    f"Runtime ALTER {table}.{column} failed "
                    f"(attempt {attempt}/{attempts}): {e}"
                )

            verified = _column_exists(db, table, column)
            if verified or verified is None:
                # Present — or unverifiable (non-SQLite dialect); do not
                # claim failure we cannot back up.
                break
            if attempt < attempts:
                continue

        final_state = _column_exists(db, table, column)
        if final_state is False:
            logger.error(
                f"Runtime column {table}.{column} could NOT be added after "
                f"{attempts} attempts; edited_at-dependent queries will fail. "
                "Apply the migration manually."
            )


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

    _ensure_runtime_columns(db)


def drop_tables(db) -> None:
    """
    Drop all messaging tables. USE WITH CAUTION.

    Args:
        db: Database instance (must be connected)
    """
    tables = [
        "msg_edit_history",
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
