"""Storage-quota tables.

Quota overrides are deliberately kept separate from ``auth_users`` so an
administrator can reset a user back to their tier default without deleting the
account, and so quota changes are auditable by ``updated_by``/``updated_at``.

Reservations make quota checks safe under concurrency. The media row does not
exist yet while bytes are being read, so a second upload could otherwise read
the same old usage and both pass the check. A short-lived reservation is
inserted before the upload starts and released in a ``finally`` block.
"""

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
CREATE TABLE IF NOT EXISTS user_storage_quotas (
    user_id INTEGER PRIMARY KEY,
    limit_bytes INTEGER,
    unlimited INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    updated_by INTEGER,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_quota_reservations (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_storage_quota_reservations_user
    ON storage_quota_reservations(user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_storage_quota_reservations_expiry
    ON storage_quota_reservations(expires_at);
"""


def ensure_schema(db) -> None:
    """Create the quota tables if they are not present yet (idempotent)."""
    for statement in split_sql_statements(SCHEMA):
        if statement.strip():
            db.execute(statement)
