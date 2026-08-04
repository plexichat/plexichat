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
    requested_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_deletion_locks_requested
    ON media_file_deletion_locks(requested_at);

-- Durable deletion tombstones. Unlike the in-transaction lock, these rows do
-- not reference media_files and therefore survive a successful row deletion or
-- a worker crash. They prevent a later raw reference from reusing an opaque
-- filename or media id after its storage object has gone away.
CREATE TABLE IF NOT EXISTS media_file_deletion_tombstones (
    media_file_id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    requested_by INTEGER NOT NULL,
    requested_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_deletion_tombstones_filename
    ON media_file_deletion_tombstones(filename);

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
    """Install the authoritative cross-domain media reference protocol.

    The media row is the serialization point. Permanent deletion locks that row
    before marking it deleted; PostgreSQL reference triggers also lock every
    matching media row before checking the tombstone. SQLite serializes writers
    with its database write lock. This prevents a reference transaction that
    started before deletion from committing after the storage object is gone.

    This function is idempotent and is called by migration 000 and media setup.
    It intentionally fails closed when the dependent schemas are incomplete,
    rather than starting with an unprotected media boundary.
    """
    required_tables = (
        "media_files",
        "media_file_deletion_locks",
        "msg_attachments",
        "stash_items",
        "stash_versions",
        "auth_users",
        "srv_servers",
        "artifacts",
    )
    missing_tables = [table for table in required_tables if not db.table_exists(table)]
    if missing_tables:
        raise RuntimeError(
            "Cannot install media reference guards; missing tables: "
            + ", ".join(missing_tables)
        )

    if getattr(db, "type", "sqlite") == "postgres":
        statements = [
            """
            CREATE OR REPLACE FUNCTION prevent_media_reference_race()
            RETURNS trigger AS $guard$
            DECLARE new_data jsonb;
            BEGIN
                new_data := to_jsonb(NEW);
                -- Lock matching media rows before observing their deletion
                -- state. This is the PostgreSQL serialization point shared
                -- with permanent deletion.
                PERFORM f.id
                FROM media_files f
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
                    OR (TG_TABLE_NAME = 'artifacts' AND (
                        COALESCE(new_data->>'payload', '') LIKE '%' || f.id::text || '%'
                    ))
                ) FOR UPDATE;
                IF EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    WHERE l.media_file_id IN (
                        SELECT f.id FROM media_files f
                        WHERE (
                            (TG_TABLE_NAME = 'msg_attachments' AND (
                                (new_data->>'checksum' IS NOT NULL AND f.checksum = new_data->>'checksum')
                                OR f.filename = new_data->>'filename'
                                OR COALESCE(new_data->>'url', '') LIKE '%' || f.filename
                                OR COALESCE(new_data->>'metadata', '') LIKE '%' || f.id::text || '%'
                            ))
                            OR (TG_TABLE_NAME IN ('stash_items', 'stash_versions') AND new_data->>'media_object_id' IN (f.id::text, f.filename))
                            OR (TG_TABLE_NAME = 'auth_users' AND COALESCE(new_data->>'avatar_url', '') LIKE '%' || f.filename)
                            OR (TG_TABLE_NAME = 'srv_servers' AND COALESCE(new_data->>'icon_url', '') LIKE '%' || f.filename)
                            OR (TG_TABLE_NAME = 'artifacts' AND COALESCE(new_data->>'payload', '') LIKE '%' || f.id::text || '%')
                        )
                    )
                ) OR EXISTS (
                    SELECT 1 FROM media_file_deletion_tombstones t
                    WHERE (TG_TABLE_NAME IN ('stash_items', 'stash_versions') AND new_data->>'media_object_id' IN (t.media_file_id::text, t.filename))
                       OR (TG_TABLE_NAME = 'msg_attachments' AND (new_data->>'filename' = t.filename OR COALESCE(new_data->>'url', '') LIKE '%' || t.filename OR COALESCE(new_data->>'metadata', '') LIKE '%' || t.media_file_id::text))
                       OR (TG_TABLE_NAME = 'auth_users' AND COALESCE(new_data->>'avatar_url', '') LIKE '%' || t.filename)
                       OR (TG_TABLE_NAME = 'srv_servers' AND COALESCE(new_data->>'icon_url', '') LIKE '%' || t.filename)
                       OR (TG_TABLE_NAME = 'artifacts' AND COALESCE(new_data->>'payload', '') LIKE '%' || t.media_file_id::text)
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
                    SELECT 1 FROM artifacts a
                    WHERE COALESCE(a.payload, '') LIKE '%' || OLD.id::text || '%'
                       OR COALESCE(a.payload, '') LIKE '%' || OLD.filename || '%'
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
                -- Recreate the triggers so existing installations receive
                -- changes to the guard protocol without a numbered migration.
                DROP TRIGGER IF EXISTS trg_media_reference_guard ON msg_attachments;
                CREATE TRIGGER trg_media_reference_guard
                BEFORE INSERT OR UPDATE ON msg_attachments
                FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();

                DROP TRIGGER IF EXISTS trg_stash_media_reference_guard ON stash_items;
                CREATE TRIGGER trg_stash_media_reference_guard
                BEFORE INSERT OR UPDATE ON stash_items
                FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();

                DROP TRIGGER IF EXISTS trg_stash_version_media_reference_guard ON stash_versions;
                CREATE TRIGGER trg_stash_version_media_reference_guard
                BEFORE INSERT OR UPDATE ON stash_versions
                FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();

                DROP TRIGGER IF EXISTS trg_auth_avatar_media_reference_guard ON auth_users;
                CREATE TRIGGER trg_auth_avatar_media_reference_guard
                BEFORE INSERT OR UPDATE OF avatar_url ON auth_users
                FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();

                DROP TRIGGER IF EXISTS trg_server_icon_media_reference_guard ON srv_servers;
                CREATE TRIGGER trg_server_icon_media_reference_guard
                BEFORE INSERT OR UPDATE OF icon_url ON srv_servers
                FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();

                DROP TRIGGER IF EXISTS trg_artifact_media_reference_guard ON artifacts;
                CREATE TRIGGER trg_artifact_media_reference_guard
                BEFORE INSERT OR UPDATE ON artifacts
                FOR EACH ROW EXECUTE FUNCTION prevent_media_reference_race();

                DROP TRIGGER IF EXISTS trg_media_delete_guard ON media_files;
                CREATE TRIGGER trg_media_delete_guard
                BEFORE DELETE ON media_files
                FOR EACH ROW EXECUTE FUNCTION prevent_referenced_media_delete();
            END
            $guard$
            """,
        ]
    else:
        # SQLite's IF NOT EXISTS would preserve stale trigger definitions on
        # existing installations. Recreate each trigger idempotently so the
        # schema-owned protocol is updated without a numbered migration.
        for trigger_name in (
            "trg_media_reference_guard",
            "trg_media_reference_guard_update",
            "trg_stash_media_reference_guard",
            "trg_stash_media_reference_guard_update",
            "trg_stash_version_media_reference_guard",
            "trg_stash_version_media_reference_guard_update",
            "trg_auth_avatar_media_reference_guard",
            "trg_auth_avatar_media_reference_guard_update",
            "trg_server_icon_media_reference_guard",
            "trg_server_icon_media_reference_guard_update",
            "trg_artifact_media_reference_guard",
            "trg_artifact_media_reference_guard_update",
            "trg_media_delete_guard",
        ):
            db.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        statements = [
            """
            CREATE TRIGGER trg_media_reference_guard
            BEFORE INSERT ON msg_attachments
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE (NEW.checksum IS NOT NULL AND f.checksum = NEW.checksum)
                       OR f.filename = NEW.filename
                       OR NEW.url LIKE '%' || f.filename
                       OR NEW.metadata LIKE '%' || f.id
                       OR EXISTS (
                           SELECT 1 FROM media_file_deletion_tombstones t
                           WHERE t.filename = NEW.filename
                              OR NEW.url LIKE '%' || t.filename
                              OR NEW.metadata LIKE '%' || t.media_file_id
                       )
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER trg_stash_media_reference_guard
            BEFORE INSERT ON stash_items
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.media_object_id IN (CAST(f.id AS TEXT), f.filename)
                       OR EXISTS (
                           SELECT 1 FROM media_file_deletion_tombstones t
                           WHERE NEW.media_object_id IN (CAST(t.media_file_id AS TEXT), t.filename)
                       )
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER trg_stash_version_media_reference_guard
            BEFORE INSERT ON stash_versions
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.media_object_id IN (CAST(f.id AS TEXT), f.filename)
                       OR EXISTS (
                           SELECT 1 FROM media_file_deletion_tombstones t
                           WHERE NEW.media_object_id IN (CAST(t.media_file_id AS TEXT), t.filename)
                       )
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER trg_auth_avatar_media_reference_guard
            BEFORE INSERT ON auth_users
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.avatar_url LIKE '%' || f.filename
                       OR EXISTS (
                           SELECT 1 FROM media_file_deletion_tombstones t
                           WHERE NEW.avatar_url LIKE '%' || t.filename
                       )
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER trg_server_icon_media_reference_guard
            BEFORE INSERT ON srv_servers
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.icon_url LIKE '%' || f.filename
                       OR EXISTS (
                           SELECT 1 FROM media_file_deletion_tombstones t
                           WHERE NEW.icon_url LIKE '%' || t.filename
                       )
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER trg_artifact_media_reference_guard
            BEFORE INSERT ON artifacts
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM media_file_deletion_locks l
                    JOIN media_files f ON f.id = l.media_file_id
                    WHERE NEW.payload LIKE '%' || f.id
                       OR NEW.payload LIKE '%' || f.filename
                       OR EXISTS (
                           SELECT 1 FROM media_file_deletion_tombstones t
                           WHERE NEW.payload LIKE '%' || t.media_file_id
                              OR NEW.payload LIKE '%' || t.filename
                       )
                ) THEN RAISE(ABORT, 'media object is being permanently deleted') END;
            END
            """,
            """
            CREATE TRIGGER trg_media_delete_guard
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
                ) OR EXISTS (
                    SELECT 1 FROM artifacts a
                    WHERE a.payload LIKE '%' || OLD.id
                       OR a.payload LIKE '%' || OLD.filename
                ) THEN RAISE(ABORT, 'media object is still referenced') END;
            END
            """,
        ]
    for statement in statements:
        db.execute(statement)
        # SQLite permits one trigger event per CREATE TRIGGER. The insert
        # definitions above are paired with update definitions here so every
        # reference mutation is covered without relying on invalid ``OR`` SQL.
        if getattr(db, "type", "sqlite") == "sqlite" and statement.lstrip().startswith(
            "CREATE TRIGGER"
        ):
            tokens = statement.split()
            trigger_name = tokens[2]
            if "BEFORE INSERT" in statement:
                update_event = "BEFORE UPDATE"
                if trigger_name == "trg_auth_avatar_media_reference_guard":
                    update_event = "BEFORE UPDATE OF avatar_url"
                elif trigger_name == "trg_server_icon_media_reference_guard":
                    update_event = "BEFORE UPDATE OF icon_url"
                update_statement = statement.replace(
                    f"CREATE TRIGGER {trigger_name}",
                    f"CREATE TRIGGER {trigger_name}_update",
                    1,
                ).replace("BEFORE INSERT", update_event, 1)
                db.execute(update_statement)


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
