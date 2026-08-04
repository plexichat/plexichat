"""
Media database schema - Table definitions for media module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


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

-- Cross-worker permanent-deletion tombstones. Reference writers reject
-- new references while a media object is being removed from storage.
CREATE TABLE IF NOT EXISTS media_file_deletion_locks (
    media_file_id INTEGER PRIMARY KEY,
    requested_by INTEGER NOT NULL,
    requested_at INTEGER NOT NULL,
    FOREIGN KEY (media_file_id) REFERENCES media_files(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_media_deletion_locks_requested
    ON media_file_deletion_locks(requested_at);

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


def create_reference_guards(db) -> None:
    """Install database-level guards for cross-domain media references.

    Application checks are useful fast paths, but they cannot protect a
    permanent delete from direct SQL or another worker. These guards run at
    the database boundary: a referenced media row cannot be deleted, and new
    attachment/Stash references cannot be created while its deletion tombstone
    is held. Migration 000 calls this only after all dependent domain tables
    exist.
    """
    if getattr(db, "type", "sqlite") == "postgres":
        statements = [
            """
            CREATE OR REPLACE FUNCTION prevent_media_reference_race()
            RETURNS trigger AS $guard$
            DECLARE new_data jsonb;
            BEGIN
                new_data := to_jsonb(NEW);
                IF EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE (
                        (TG_TABLE_NAME = 'msg_attachments' AND (
                            (new_data->>'checksum' IS NOT NULL AND f.checksum = new_data->>'checksum')
                            OR f.filename = new_data->>'filename'
                            OR COALESCE(new_data->>'url', '') LIKE '%' || f.filename
                            OR COALESCE(new_data->>'metadata', '') LIKE '%' || f.id::text || '%'
                        ))
                        OR (TG_TABLE_NAME IN ('stash_items', 'stash_versions') AND (
                            new_data->>'media_object_id' IN (f.id::text, f.filename)
                        ))
                        OR (TG_TABLE_NAME = 'auth_users' AND (
                            COALESCE(new_data->>'avatar_url', '') LIKE '%' || f.filename
                        ))
                        OR (TG_TABLE_NAME = 'srv_servers' AND (
                            COALESCE(new_data->>'icon_url', '') LIKE '%' || f.filename
                        ))
                    )
                ) THEN
                    RAISE EXCEPTION 'media object is being permanently deleted';
                END IF;
                RETURN NEW;
            END;
            $guard$ LANGUAGE plpgsql
            """,
            """
            CREATE OR REPLACE FUNCTION prevent_referenced_media_delete()
            RETURNS trigger AS $guard$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM msg_attachments a
                    WHERE a.deleted = 0 AND (
                        (OLD.checksum IS NOT NULL AND a.checksum = OLD.checksum)
                        OR a.filename = OLD.filename
                        OR a.url LIKE '%' || OLD.filename
                        OR a.metadata LIKE '%' || OLD.id::text || '%'
                    )
                ) OR EXISTS (
                    SELECT 1 FROM stash_items i
                    WHERE i.deleted = 0
                      AND i.media_object_id IN (OLD.id::text, OLD.filename)
                ) OR EXISTS (
                    SELECT 1 FROM stash_versions v
                    JOIN stash_items i ON i.id = v.item_id
                    WHERE i.deleted = 0
                      AND v.media_object_id IN (OLD.id::text, OLD.filename)
                ) OR EXISTS (
                    SELECT 1 FROM auth_users u
                    WHERE u.avatar_url LIKE '%' || OLD.filename
                ) OR EXISTS (
                    SELECT 1 FROM srv_servers s
                    WHERE s.deleted = 0 AND s.icon_url LIKE '%' || OLD.filename
                ) THEN
                    RAISE EXCEPTION 'media object is still referenced';
                END IF;
                RETURN OLD;
            END;
            $guard$ LANGUAGE plpgsql
            """,
            """
            DO $guard$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_media_reference_guard') THEN
                    CREATE TRIGGER trg_media_reference_guard
                    BEFORE INSERT OR UPDATE ON msg_attachments
                    FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_stash_media_reference_guard') THEN
                    CREATE TRIGGER trg_stash_media_reference_guard
                    BEFORE INSERT OR UPDATE ON stash_items
                    FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_stash_version_media_reference_guard') THEN
                    CREATE TRIGGER trg_stash_version_media_reference_guard
                    BEFORE INSERT OR UPDATE ON stash_versions
                    FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_auth_avatar_media_reference_guard') THEN
                    CREATE TRIGGER trg_auth_avatar_media_reference_guard
                    BEFORE UPDATE OF avatar_url ON auth_users
                    FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_server_icon_media_reference_guard') THEN
                    CREATE TRIGGER trg_server_icon_media_reference_guard
                    BEFORE UPDATE OF icon_url ON srv_servers
                    FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_media_delete_guard') THEN
                    CREATE TRIGGER trg_media_delete_guard
                    BEFORE DELETE ON media_files
                    FOR EACH ROW EXECUTE FUNCTION prevent_referenced_media_delete();
                END IF;
            END
            $guard$
            """,
        ]
    else:
        statements = [
            """
            CREATE TRIGGER IF NOT EXISTS trg_media_reference_guard
            BEFORE INSERT OR UPDATE ON msg_attachments
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE (NEW.checksum IS NOT NULL AND f.checksum = NEW.checksum)
                       OR f.filename = NEW.filename
                       OR NEW.url LIKE '%' || f.filename
                       OR NEW.metadata LIKE '%' || f.id
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_stash_media_reference_guard
            BEFORE INSERT OR UPDATE ON stash_items
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.media_object_id IN (CAST(f.id AS TEXT), f.filename)
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_stash_version_media_reference_guard
            BEFORE INSERT OR UPDATE ON stash_versions
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.media_object_id IN (CAST(f.id AS TEXT), f.filename)
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_auth_avatar_media_reference_guard
            BEFORE UPDATE OF avatar_url ON auth_users
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.avatar_url LIKE '%' || f.filename
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_server_icon_media_reference_guard
            BEFORE UPDATE OF icon_url ON srv_servers
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.icon_url LIKE '%' || f.filename
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_media_delete_guard
            BEFORE DELETE ON media_files
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM msg_attachments a
                    WHERE a.deleted = 0 AND (
                        (OLD.checksum IS NOT NULL AND a.checksum = OLD.checksum)
                        OR a.filename = OLD.filename
                        OR a.url LIKE '%' || OLD.filename
                        OR a.metadata LIKE '%' || OLD.id
                    )
                ) OR EXISTS (
                    SELECT 1 FROM stash_items i
                    WHERE i.deleted = 0
                      AND i.media_object_id IN (CAST(OLD.id AS TEXT), OLD.filename)
                ) OR EXISTS (
                    SELECT 1 FROM stash_versions v
                    JOIN stash_items i ON i.id = v.item_id
                    WHERE i.deleted = 0
                      AND v.media_object_id IN (CAST(OLD.id AS TEXT), OLD.filename)
                ) OR EXISTS (
                    SELECT 1 FROM auth_users u
                    WHERE u.avatar_url LIKE '%' || OLD.filename
                ) OR EXISTS (
                    SELECT 1 FROM srv_servers s
                    WHERE s.deleted = 0 AND s.icon_url LIKE '%' || OLD.filename
                ) THEN RAISE(ABORT, 'media object is still referenced') END;
            END
            """,
        ]
    for statement in statements:
        db.execute(statement)


def create_tables(db):
    """Create all media tables."""
    statements = split_sql_statements(SCHEMA)

    for statement in statements:
        if statement:
            try:
                converted = (
                    db.convert_schema(statement)
                    if hasattr(db, "convert_schema")
                    else statement
                )
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    # Migration 000 invokes guards after all dependent domain schemas exist.
    # Keep this function limited to media-owned tables for direct module setup.
    logger.info("Media tables created successfully")
