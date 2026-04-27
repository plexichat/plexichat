"""
Add encryption configuration for polls, descriptions, and internal notes.

This migration documents the new encryption configuration options that should be
added to config/config.yaml. These are feature flags to enable encryption for
different data types after the corresponding migrations are applied.

Configuration to add to config/config.yaml:
  encryption:
    encrypt_polls: false  # Enable after migration 025
    encrypt_descriptions: false  # Enable after migration 029
    encrypt_thread_names: false  # Enable after migration 029
    encrypt_internal_notes: false  # Enable after migration 033
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """
    Apply migration - document configuration changes.

    This is a documentation-only migration. The actual configuration
    should be added to config/config.yaml manually.
    """
    logger.info(
        "Migration 022: Documenting encryption configuration options. "
        "Please add the following to config/config.yaml:\n"
        "  encryption:\n"
        "    encrypt_polls: false\n"
        "    encrypt_descriptions: false\n"
        "    encrypt_thread_names: false\n"
        "    encrypt_internal_notes: false"
    )


def down(db):
    """
    Rollback - remove configuration options.

    Remove the encryption configuration options from config/config.yaml.
    """
    logger.info(
        "Migration 022 rollback: Remove encryption configuration options from config.yaml"
    )
