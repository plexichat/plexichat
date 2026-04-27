"""
Add database query metrics to telemetry tracking.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 009: Starting DB metrics addition to telemetry")
    # 1. Add db_queries column
    try:
        if not db.column_exists("telemetry_response_times", "db_queries"):
            db.execute(
                "ALTER TABLE telemetry_response_times ADD COLUMN db_queries INTEGER DEFAULT 0"
            )
        if not db.column_exists("telemetry_response_times", "db_time_ms"):
            db.execute(
                "ALTER TABLE telemetry_response_times ADD COLUMN db_time_ms REAL DEFAULT 0.0"
            )
    except Exception as e:
        print(f"Warning: Could not add telemetry columns: {e}")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 009 rollback: Starting rollback")
    if db.type == "postgres":
        try:
            if db.column_exists("telemetry_response_times", "db_queries"):
                db.execute(
                    "ALTER TABLE telemetry_response_times DROP COLUMN db_queries"
                )
            if db.column_exists("telemetry_response_times", "db_time_ms"):
                db.execute(
                    "ALTER TABLE telemetry_response_times DROP COLUMN db_time_ms"
                )
        except Exception:
            pass
