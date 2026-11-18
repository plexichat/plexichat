"""
Migration 008: composite indexes for hot auth lookup paths.

Two auth reads do not have an access path that matches their filter/sort:

* the password-reset throttle counts recent (user_id, token_type) email tokens
  with a ``created_at`` range, and the verification flow reads the newest one;
  with no index on ``auth_email_tokens`` both scan every token ever issued;
* the per-user audit feed filters by ``user_id`` and orders by ``timestamp``;
  the single-column index finds the rows but forces a temporary sort B-tree
  (``USE TEMP B-TREE FOR ORDER BY``).

A composite index whose leading columns are the equality filters and whose
trailing column is the sort/range key serves both queries in one ordered scan.
These index definitions mirror the authoritative ones in the auth schema so
fresh installs and already-migrated databases converge on the same shape.
``CREATE INDEX IF NOT EXISTS`` keeps the migration idempotent on SQLite and
PostgreSQL.
"""

import logging

from src.core.security.hardening import require_identifier

logger = logging.getLogger(__name__)


# (index name, table name, ordered columns). All entries are static literals;
# the identifiers are still passed through require_identifier at the point of
# use so the DDL identifier guard audit does not need an allowlist exception.
INDEXES = (
    (
        "idx_auth_email_tokens_user_type_created",
        "auth_email_tokens",
        ("user_id", "token_type", "created_at"),
    ),
    (
        "idx_auth_audit_user_timestamp",
        "auth_audit_log",
        ("user_id", "timestamp"),
    ),
)


def up(db):
    """Create the composite auth lookup indexes (idempotent on both engines)."""
    for name, table, columns in INDEXES:
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {require_identifier(name, 'index name')} "
            f"ON {require_identifier(table, 'table name')} "
            f"({', '.join(require_identifier(col, 'column name') for col in columns)})"
        )
    logger.info("Migration 008: created %d composite auth lookup indexes", len(INDEXES))


def down(db):
    """Drop only the indexes this migration created."""
    for name, _table, _columns in INDEXES:
        try:
            db.execute(
                f"DROP INDEX IF EXISTS {require_identifier(name, 'index name')}"
            )
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
