
def _add_column_if_missing(db, table: str, column: str, ddl: str) -> None:
    try:
        if db.type == "postgres":
            rows = db.fetch_all(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
            existing = {row["column_name"] for row in rows}
        else:
            rows = db.fetch_all(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in rows}
        if column in existing:
            return
    except Exception:
        pass
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    except Exception:
        pass


def up(db):
    try:
        # Add deletion columns to auth_users
        # deletion_status: active, frozen, purged
        _add_column_if_missing(db, "auth_users", "deletion_status", "deletion_status TEXT NOT NULL DEFAULT 'active'")
        _add_column_if_missing(db, "auth_users", "deletion_at", "deletion_at BIGINT")
        
        # Create a database backup of deletion records for last-resort lookup
        # This mirrors the external audit log for redundancy
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_deletion_records (
                id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                identifier_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                scheduled_at BIGINT NOT NULL,
                purged_at BIGINT,
                audit_log_checksum TEXT,
                UNIQUE(user_id)
            )
            """
        )
        
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_users_deletion_status ON auth_users(deletion_status)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_users_deletion_at ON auth_users(deletion_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_deletion_records_user ON auth_deletion_records(user_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_deletion_records_hash ON auth_deletion_records(identifier_hash)"
        )
    except Exception:
        pass


def down(db):
    # Not removing columns in down to prevent accidental data loss during rollback experiments
    pass
