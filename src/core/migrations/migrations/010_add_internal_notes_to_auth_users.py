"""
Add internal_notes column to auth_users table.
"""

def up(db):
    """Apply the migration."""
    if db.type == "sqlite":
        rows = db.fetch_all("PRAGMA table_info(auth_users)")
        columns = [row["name"] for row in rows]
        if "internal_notes" not in columns:
            db.execute("ALTER TABLE auth_users ADD COLUMN internal_notes TEXT")
    else:
        # Postgres
        row = db.fetch_one("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='auth_users' AND column_name='internal_notes'
        """)
        if not row:
            db.execute("ALTER TABLE auth_users ADD COLUMN internal_notes TEXT")

def down(db):
    """Rollback the migration."""
    if db.type != "sqlite":
        try:
            db.execute("ALTER TABLE auth_users DROP COLUMN IF EXISTS internal_notes")
        except Exception:
            pass
