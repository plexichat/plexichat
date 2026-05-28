"""
Selftest no-op migration for testing apply/rollback.

This migration does nothing — it's designed purely for the self-test
system to verify that apply and rollback operations work correctly.

Depends: 036

Version: 037
"""

from typing import Any, Dict


def up(db) -> Dict[str, Any]:
    """
    No-op upward migration.

    Does not modify the database in any way. The self-test system uses this
    to verify that the apply-migration and rollback-migration endpoints work
    correctly with a known, safe migration.
    """
    return {
        "success": True,
        "message": "Selftest no-op migration applied (037)",
    }


def down(db) -> Dict[str, Any]:
    """
    No-op rollback.

    Does not modify the database. Reverses the no-op migration.
    """
    return {
        "success": True,
        "message": "Selftest no-op migration rolled back (037)",
    }
