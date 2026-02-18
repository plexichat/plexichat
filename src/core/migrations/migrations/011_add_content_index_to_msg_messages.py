"""
Add content_index column to msg_messages table for encrypted search.
"""


def up(db):
    """Apply the migration."""
    # 1. Add column if it doesn't exist
    if db.type == "sqlite":
        rows = db.fetch_all("PRAGMA table_info(msg_messages)")
        columns = [row["name"] for row in rows]
        if "content_index" not in columns:
            db.execute("ALTER TABLE msg_messages ADD COLUMN content_index TEXT")
    else:
        # Postgres
        row = db.fetch_one("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='msg_messages' AND column_name='content_index'
        """)
        if not row:
            db.execute("ALTER TABLE msg_messages ADD COLUMN content_index TEXT")

    # 2. Add index
    if db.type == "sqlite":
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_messages_content_index ON msg_messages(content_index)"
        )
    else:
        # Postgres index creation is already idempotent with IF NOT EXISTS in our dialect usually,
        # but let's be explicit
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_messages_content_index ON msg_messages(content_index)"
        )


def down(db):
    """Rollback the migration."""
    if db.type == "sqlite":
        # SQLite doesn't support DROP COLUMN easily in older versions,
        # and usually we don't rollback columns in production anyway
        pass
    else:
        # Postgres
        try:
            db.execute("DROP INDEX IF EXISTS idx_msg_messages_content_index")
            db.execute("ALTER TABLE msg_messages DROP COLUMN IF EXISTS content_index")
        except Exception:
            pass
