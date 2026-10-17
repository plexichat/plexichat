"""
Schema for DSAR (Data Subject Access Request) module.
GDPR Article 20 Right to Data Portability exports.
"""

from src.core.database.core.schema_splitter import split_sql_statements

SCHEMA_SQLITE = """
-- DSAR requests table
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
);

CREATE INDEX IF NOT EXISTS idx_dsar_requests_user ON dsar_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_dsar_requests_status ON dsar_requests(status);
CREATE INDEX IF NOT EXISTS idx_dsar_requests_expires ON dsar_requests(expires_at);

-- DSAR export manifest
CREATE TABLE IF NOT EXISTS dsar_export_manifest (
    id BIGINT PRIMARY KEY,
    request_id BIGINT NOT NULL,
    table_name TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    exported_at BIGINT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES dsar_requests(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dsar_manifest_request ON dsar_export_manifest(request_id);
"""


def create_tables(db) -> None:
    statements = split_sql_statements(SCHEMA_SQLITE)
    for statement in statements:
        converted = (
            db.convert_schema(statement) if hasattr(db, "convert_schema") else statement
        )
        db.execute(converted)
