"""
Feature expansion migration - Add tables and columns for new features:
- DM threaded conversations (thread_threads gets conversation_id for DMs)
- Voice messages (msg_messages gets voice metadata columns)
- Message forwarding (new forwarded_messages table)
- Scheduled messages (new scheduled_messages table)
- Message bookmarks per user (new user_bookmarks table)
- User profiles with custom status (new user_profiles table, auth_users gets columns)
- Report flow enhancement (reports table gets category, evidence, status tracking)
- Thread slowmode (thread_threads gets slowmode columns)
- DM anti-spam (new dm_spam_filters table)
- Webhook retry queue (new webhook_retry_queue table)
- Push notification tokens (new push_tokens table)
- Last chat tracking (new user_last_chat table)
"""


def _add_column_if_missing(db, table: str, column: str, ddl: str) -> None:
    """Add a column to a table if it doesn't already exist."""
    try:
        if db.type == "postgres":
            rows = db.fetch_all(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table,),
            )
            existing = {row["column_name"] for row in rows}
        else:
            rows = db.fetch_all(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in rows}
        if column in existing:
            return
    except Exception:
        pass
    try:
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier(table, db.type)
        db.execute(f"ALTER TABLE {safe_table} ADD COLUMN {ddl}")
    except Exception:
        pass


def up(db):
    """Apply the migration."""
    # === Voice Messages ===
    _add_column_if_missing(
        db, "msg_messages", "voice_duration_ms", "voice_duration_ms INTEGER"
    )
    _add_column_if_missing(db, "msg_messages", "voice_waveform", "voice_waveform TEXT")
    _add_column_if_missing(
        db, "msg_messages", "message_type", "message_type TEXT NOT NULL DEFAULT 'text'"
    )

    # === Message Forwarding ===
    db.execute("""
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
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_fwd_message ON msg_forwarded(message_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_fwd_original ON msg_forwarded(original_message_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_fwd_by ON msg_forwarded(forwarded_by)"
    )

    # === Scheduled Messages ===
    db.execute("""
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
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_sched_conv ON msg_scheduled(conversation_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_sched_author ON msg_scheduled(author_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_sched_at ON msg_scheduled(scheduled_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_sched_status ON msg_scheduled(status)"
    )

    # === User Bookmarks (per-user message pins) ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_bookmarks (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            label TEXT,
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, message_id)
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookmark_user ON user_bookmarks(user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookmark_message ON user_bookmarks(message_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookmark_conv ON user_bookmarks(conversation_id)"
    )

    # === User Profiles with custom status ===
    db.execute("""
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
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_profile_user ON user_profiles(user_id)")
    _add_column_if_missing(
        db, "auth_users", "custom_status_text", "custom_status_text TEXT"
    )
    _add_column_if_missing(
        db, "auth_users", "custom_status_emoji", "custom_status_emoji TEXT"
    )
    _add_column_if_missing(
        db, "auth_users", "custom_status_expires_at", "custom_status_expires_at INTEGER"
    )

    # === Report Flow Enhancement ===
    _add_column_if_missing(db, "reports", "category", "category TEXT")
    _add_column_if_missing(db, "reports", "evidence_ids", "evidence_ids TEXT")
    _add_column_if_missing(
        db, "reports", "status", "status TEXT NOT NULL DEFAULT 'open'"
    )
    _add_column_if_missing(
        db, "reports", "priority", "priority TEXT NOT NULL DEFAULT 'medium'"
    )
    _add_column_if_missing(db, "reports", "assigned_to", "assigned_to INTEGER")
    _add_column_if_missing(db, "reports", "resolution", "resolution TEXT")
    _add_column_if_missing(db, "reports", "resolved_at", "resolved_at INTEGER")
    _add_column_if_missing(db, "reports", "resolved_by", "resolved_by INTEGER")
    _add_column_if_missing(db, "reports", "admin_notes", "admin_notes TEXT")
    _add_column_if_missing(db, "reports", "message_content", "message_content TEXT")
    _add_column_if_missing(
        db, "reports", "reported_user_id", "reported_user_id INTEGER"
    )
    # Try to create the reports table if it doesn't exist yet
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY,
                reporter_id INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                channel_id INTEGER,
                server_id INTEGER,
                reason TEXT NOT NULL,
                category TEXT,
                details TEXT,
                evidence_ids TEXT,
                message_content TEXT,
                reported_user_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'medium',
                assigned_to INTEGER,
                resolution TEXT,
                resolved_at INTEGER,
                resolved_by INTEGER,
                admin_notes TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_reporter ON reports(reporter_id)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_server ON reports(server_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_assigned ON reports(assigned_to)"
        )
    except Exception:
        pass

    # === Thread Slowmode ===
    _add_column_if_missing(
        db,
        "thread_threads",
        "slowmode_seconds",
        "slowmode_seconds INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        db, "thread_threads", "slowmode_last_msg", "slowmode_last_msg INTEGER"
    )
    # Slowmode per-user tracking via thread_members
    _add_column_if_missing(
        db, "thread_members", "slowmode_until", "slowmode_until INTEGER"
    )

    # === DM Anti-Spam ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS dm_spam_filters (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            target_user_id INTEGER,
            pattern TEXT,
            filter_type TEXT NOT NULL DEFAULT 'rate',
            action TEXT NOT NULL DEFAULT 'warn',
            threshold INTEGER NOT NULL DEFAULT 5,
            window_seconds INTEGER NOT NULL DEFAULT 60,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_spam_user ON dm_spam_filters(user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_spam_target ON dm_spam_filters(target_user_id)"
    )

    db.execute("""
        CREATE TABLE IF NOT EXISTS dm_spam_events (
            id INTEGER PRIMARY KEY,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            content_hash TEXT,
            created_at INTEGER NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_spam_evt_sender ON dm_spam_events(sender_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_spam_evt_created ON dm_spam_events(created_at)"
    )

    # === Webhook Retry Queue ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS webhook_retry_queue (
            id INTEGER PRIMARY KEY,
            webhook_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            next_retry_at INTEGER NOT NULL,
            last_error TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_wh_retry_webhook ON webhook_retry_queue(webhook_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_wh_retry_next ON webhook_retry_queue(next_retry_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_wh_retry_status ON webhook_retry_queue(status)"
    )

    # === Push Notification Tokens ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS push_tokens (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL,
            platform TEXT NOT NULL,
            device_id TEXT,
            app_version TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_used_at INTEGER,
            UNIQUE(user_id, token)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_push_token_user ON push_tokens(user_id)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_token_platform ON push_tokens(platform)"
    )

    # === Last Chat Tracking ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_last_chat (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE,
            conversation_id INTEGER NOT NULL,
            last_message_id INTEGER,
            scroll_position INTEGER,
            updated_at INTEGER NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_last_chat_user ON user_last_chat(user_id)"
    )

    # === Recent Chat History ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_recent_chats (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            accessed_at INTEGER NOT NULL,
            unread_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_recent_chats_user ON user_recent_chats(user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_recent_chats_user_conv ON user_recent_chats(user_id, conversation_id)"
    )


def down(db):
    """Rollback the migration - drop all new tables."""
    tables = [
        "msg_forwarded",
        "msg_scheduled",
        "user_bookmarks",
        "user_profiles",
        "dm_spam_filters",
        "dm_spam_events",
        "webhook_retry_queue",
        "push_tokens",
        "user_last_chat",
    ]
    for table in tables:
        try:
            db.execute(f"DROP TABLE IF EXISTS {table}")
        except Exception:
            pass

    # Also drop recent chats table
    try:
        db.execute("DROP TABLE IF EXISTS user_recent_chats")
    except Exception:
        pass
