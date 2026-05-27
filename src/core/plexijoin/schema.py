"""
Database schema for PlexiJoin federation module.

Tables are created by migration 035_plexijoin_federation_system.py.
This file exists for consistency with other modules.
"""


def create_tables(db) -> None:
    """
    Ensure PlexiJoin tables exist.

    This is a no-op since tables are created by migration 035.
    The function exists for consistency with other modules.

    Args:
        db: Database instance
    """
    pass


def drop_tables(db) -> None:
    """
    Drop all PlexiJoin tables. USE WITH CAUTION.

    Args:
        db: Database instance
    """
    tables = [
        "plexijoin_traffic_log",
        "plexijoin_inbound_requests",
        "plexijoin_connections",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
