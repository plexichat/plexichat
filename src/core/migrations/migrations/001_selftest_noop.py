"""
Selftest no-op migration.

A no-op migration used by the self-test suite to verify
migration apply/rollback endpoints work correctly.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """No-op migration — does nothing."""
    logger.info("Migration 001: selftest no-op (nothing to do)")


def down(db):
    """No-op rollback — does nothing."""
    logger.info("Migration 001 rollback: selftest no-op (nothing to undo)")
