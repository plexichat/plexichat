"""
Add DSAR (Data Subject Access Request) tables for GDPR compliance.

Creates the dsar_requests table to track data access requests from users
and the dsar_export_manifest table to track exported data per table.

Version: 044
Depends: 043
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    logger.info("Migration 044: Starting DSAR tables creation")

    if not db.table_exists("dsar_requests"):
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS dsar_requests (
                    id BIGINT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_at BIGINT NOT NULL,
                    completed_at BIGINT,
                    expires_at BIGINT,
                    format TEXT NOT NULL DEFAULT 'json',
                    storage_backend TEXT NOT NULL DEFAULT 'local',
                    storage_path TEXT,
                    checksum TEXT,
                    file_size_bytes BIGINT,
                    admin_id BIGINT,
                    denial_reason TEXT,
                    error_message TEXT,
                    metadata TEXT
                )
                """
            )
            logger.info("Migration 044: Created dsar_requests table")
        except Exception as e:
            logger.warning(f"Migration 044: Failed to create dsar_requests table: {e}")
    else:
        logger.info("Migration 044: dsar_requests table already exists, skipping")
        # Add storage_backend/storage_path columns to existing tables (idempotent)
        try:
            existing_cols = {
                row["name"] if isinstance(row, dict) else row[1]
                for row in db.fetch_all("PRAGMA table_info(dsar_requests)")
            }
        except Exception:
            existing_cols = set()

        if "storage_backend" not in existing_cols:
            try:
                db.execute(
                    "ALTER TABLE dsar_requests ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'local'"
                )
                logger.info("Migration 044: Added storage_backend column")
            except Exception as e:
                logger.warning(
                    f"Migration 044: Failed to add storage_backend column: {e}"
                )
        if "storage_path" not in existing_cols:
            try:
                db.execute("ALTER TABLE dsar_requests ADD COLUMN storage_path TEXT")
                logger.info("Migration 044: Added storage_path column")
            except Exception as e:
                logger.warning(f"Migration 044: Failed to add storage_path column: {e}")

    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dsar_requests_user_id ON dsar_requests(user_id)"
        )
        logger.info("Migration 044: Created idx_dsar_requests_user_id index")
    except Exception as e:
        logger.warning(
            f"Migration 044: Failed to create idx_dsar_requests_user_id index: {e}"
        )

    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dsar_requests_status ON dsar_requests(status)"
        )
        logger.info("Migration 044: Created idx_dsar_requests_status index")
    except Exception as e:
        logger.warning(
            f"Migration 044: Failed to create idx_dsar_requests_status index: {e}"
        )

    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dsar_requests_expires_at ON dsar_requests(expires_at)"
        )
        logger.info("Migration 044: Created idx_dsar_requests_expires_at index")
    except Exception as e:
        logger.warning(
            f"Migration 044: Failed to create idx_dsar_requests_expires_at index: {e}"
        )

    if not db.table_exists("dsar_export_manifest"):
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS dsar_export_manifest (
                    id BIGINT PRIMARY KEY,
                    request_id BIGINT NOT NULL,
                    table_name TEXT NOT NULL,
                    record_count INTEGER NOT NULL DEFAULT 0,
                    exported_at BIGINT NOT NULL
                )
                """
            )
            logger.info("Migration 044: Created dsar_export_manifest table")
        except Exception as e:
            logger.warning(
                f"Migration 044: Failed to create dsar_export_manifest table: {e}"
            )
    else:
        logger.info(
            "Migration 044: dsar_export_manifest table already exists, skipping"
        )

    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dsar_export_manifest_request_id ON dsar_export_manifest(request_id)"
        )
        logger.info("Migration 044: Created idx_dsar_export_manifest_request_id index")
    except Exception as e:
        logger.warning(
            f"Migration 044: Failed to create idx_dsar_export_manifest_request_id index: {e}"
        )

    logger.info("Migration 044 completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 044 rollback: Starting rollback")

    try:
        if db.table_exists("dsar_export_manifest"):
            db.execute("DROP TABLE IF EXISTS dsar_export_manifest")
            logger.info("Migration 044 rollback: Dropped dsar_export_manifest table")
    except Exception as e:
        logger.warning(
            f"Migration 044 rollback: Failed to drop dsar_export_manifest table: {e}"
        )

    try:
        if db.table_exists("dsar_requests"):
            db.execute("DROP TABLE IF EXISTS dsar_requests")
            logger.info("Migration 044 rollback: Dropped dsar_requests table")
    except Exception as e:
        logger.warning(
            f"Migration 044 rollback: Failed to drop dsar_requests table: {e}"
        )

    try:
        db.execute("DROP INDEX IF EXISTS idx_dsar_requests_user_id")
    except Exception as e:
        logger.warning(
            f"Migration 044 rollback: Failed to drop idx_dsar_requests_user_id: {e}"
        )

    try:
        db.execute("DROP INDEX IF EXISTS idx_dsar_requests_status")
    except Exception as e:
        logger.warning(
            f"Migration 044 rollback: Failed to drop idx_dsar_requests_status: {e}"
        )

    try:
        db.execute("DROP INDEX IF EXISTS idx_dsar_requests_expires_at")
    except Exception as e:
        logger.warning(
            f"Migration 044 rollback: Failed to drop idx_dsar_requests_expires_at: {e}"
        )

    try:
        db.execute("DROP INDEX IF EXISTS idx_dsar_export_manifest_request_id")
    except Exception as e:
        logger.warning(
            f"Migration 044 rollback: Failed to drop idx_dsar_export_manifest_request_id: {e}"
        )

    logger.info("Migration 044 rollback completed")
