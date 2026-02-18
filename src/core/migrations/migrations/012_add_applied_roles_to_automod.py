"""
Add applied_roles column to automod_rules table.
"""


def up(db):
    """Apply the migration."""
    try:
        if db.type == "sqlite":
            rows = db.fetch_all("PRAGMA table_info(automod_rules)")
            columns = [row["name"] for row in rows]
            if "applied_roles" not in columns:
                db.execute(
                    "ALTER TABLE automod_rules ADD COLUMN applied_roles TEXT DEFAULT '[]'"
                )
        else:
            # Postgres
            row = db.fetch_one("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='automod_rules' AND column_name='applied_roles'
            """)
            if not row:
                db.execute(
                    "ALTER TABLE automod_rules ADD COLUMN applied_roles TEXT DEFAULT '[]'"
                )
    except Exception:
        # Ignore errors if column already exists or other issues
        pass


def down(db):
    """Rollback the migration."""
    if db.type == "sqlite":
        pass
    else:
        # Postgres
        try:
            db.execute("ALTER TABLE automod_rules DROP COLUMN IF EXISTS applied_roles")
        except Exception:
            pass
