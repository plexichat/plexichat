"""
Add artifacts tables for the Plexichat Artifacts feature.

Creates the artifacts, voice_calls, and artifact_ops tables which back
first-class persistent records for voice calls, whiteboards, uploads,
files, transcripts, and future artifact types.

Version: 047
Depends: 046
"""

import logging

logger = logging.getLogger(__name__)

from src.core.artifacts.schema import (  # noqa: E402
    create_tables as create_artifacts_tables,
)


def up(db):
    """Apply the artifacts tables migration."""
    logger.info("Migration 047: Starting artifacts tables creation")

    try:
        create_artifacts_tables(db)
        logger.info("Migration 047: Artifacts tables created successfully")
    except Exception as e:
        logger.error(f"Migration 047: Failed to create artifacts tables: {e}")
        raise

    logger.info("Migration 047 completed successfully")


def down(db):
    """Rollback the artifacts tables migration."""
    logger.info("Migration 047 rollback: Starting rollback")

    for table in ("artifact_ops", "voice_calls", "artifacts"):
        try:
            db.execute(f"DROP TABLE IF EXISTS {table}")
            logger.info(f"Migration 047 rollback: Dropped {table} table")
        except Exception as e:
            logger.warning(f"Migration 047 rollback: Failed to drop {table} table: {e}")

    logger.info("Migration 047 rollback completed")
