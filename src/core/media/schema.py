"""
Media database schema - Table definitions for media module.
"""

import utils.logger as logger


SCHEMA = """
-- Media files table
CREATE TABLE IF NOT EXISTS media_files (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    storage_backend TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    checksum TEXT,
    uploaded_by INTEGER NOT NULL,
    uploaded_at INTEGER NOT NULL,
    metadata TEXT,
    scan_status TEXT NOT NULL DEFAULT 'pending',
    scan_result TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at INTEGER
);

-- Thumbnails table
CREATE TABLE IF NOT EXISTS media_thumbnails (
    id INTEGER PRIMARY KEY,
    media_file_id INTEGER NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (media_file_id) REFERENCES media_files(id) ON DELETE CASCADE,
    UNIQUE(media_file_id, size)
);

-- Proxied content cache table
CREATE TABLE IF NOT EXISTS media_proxy_cache (
    id INTEGER PRIMARY KEY,
    source_url TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    checksum TEXT,
    cached_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_accessed INTEGER NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_media_files_uploaded_by ON media_files(uploaded_by);
CREATE INDEX IF NOT EXISTS idx_media_files_content_type ON media_files(content_type);
CREATE INDEX IF NOT EXISTS idx_media_files_storage_backend ON media_files(storage_backend);
CREATE INDEX IF NOT EXISTS idx_media_files_scan_status ON media_files(scan_status);
CREATE INDEX IF NOT EXISTS idx_media_thumbnails_file ON media_thumbnails(media_file_id);
CREATE INDEX IF NOT EXISTS idx_media_proxy_cache_url ON media_proxy_cache(source_url);
CREATE INDEX IF NOT EXISTS idx_media_proxy_cache_expires ON media_proxy_cache(expires_at);
"""


def create_tables(db):
    """Create all media tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]

    for statement in statements:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Media tables created successfully")
