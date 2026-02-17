"""
Add timeout columns to srv_members table.
"""

def up(db):
    """Apply the migration."""
    try:
        if db.type == "sqlite":
            rows = db.fetch_all("PRAGMA table_info(srv_members)")
            columns = [row["name"] for row in rows]
            if "timeout_until" not in columns:
                db.execute("ALTER TABLE srv_members ADD COLUMN timeout_until BIGINT")
            if "timeout_reason" not in columns:
                db.execute("ALTER TABLE srv_members ADD COLUMN timeout_reason TEXT")
        else:
            # Postgres
            row = db.fetch_one("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='srv_members' AND column_name='timeout_until'
            """)
            if not row:
                db.execute("ALTER TABLE srv_members ADD COLUMN timeout_until BIGINT")
            
            row = db.fetch_one("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='srv_members' AND column_name='timeout_reason'
            """)
            if not row:
                db.execute("ALTER TABLE srv_members ADD COLUMN timeout_reason TEXT")
    except Exception:
        pass

def down(db):
    """Rollback the migration."""
    if db.type == "postgres":
        try:
            db.execute("ALTER TABLE srv_members DROP COLUMN IF EXISTS timeout_until")
            db.execute("ALTER TABLE srv_members DROP COLUMN IF EXISTS timeout_reason")
        except Exception:
            pass
