"""
Add database query metrics to telemetry tracking.
"""

def up(db):
    """Apply the migration."""
    # 1. Add db_queries column
    try:
        if db.type == "sqlite":
            rows = db.fetch_all("PRAGMA table_info(telemetry_response_times)")
            columns = [row["name"] for row in rows]
            if "db_queries" not in columns:
                db.execute("ALTER TABLE telemetry_response_times ADD COLUMN db_queries INTEGER DEFAULT 0")
            if "db_time_ms" not in columns:
                db.execute("ALTER TABLE telemetry_response_times ADD COLUMN db_time_ms REAL DEFAULT 0.0")
        else:
            # Postgres
            row = db.fetch_one("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='telemetry_response_times' AND column_name='db_queries'
            """)
            if not row:
                db.execute("ALTER TABLE telemetry_response_times ADD COLUMN db_queries INTEGER DEFAULT 0")
            
            row = db.fetch_one("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='telemetry_response_times' AND column_name='db_time_ms'
            """)
            if not row:
                db.execute("ALTER TABLE telemetry_response_times ADD COLUMN db_time_ms REAL DEFAULT 0.0")
    except Exception as e:
        print(f"Warning: Could not add telemetry columns: {e}")

def down(db):
    """Rollback the migration."""
    if db.type != "sqlite":
        try:
            db.execute("ALTER TABLE telemetry_response_times DROP COLUMN IF EXISTS db_queries")
            db.execute("ALTER TABLE telemetry_response_times DROP COLUMN IF EXISTS db_time_ms")
        except Exception:
            pass
