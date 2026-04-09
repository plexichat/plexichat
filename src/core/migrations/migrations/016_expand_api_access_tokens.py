def _add_column_if_missing(db, table: str, column: str, ddl: str) -> None:
    try:
        rows = db.fetch_all(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in rows}
        if column in existing:
            return
    except Exception:
        pass
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    except Exception:
        pass


def up(db):
    try:
        _add_column_if_missing(
            db, "auth_api_access_tokens", "description", "description TEXT"
        )
        _add_column_if_missing(
            db, "auth_api_access_tokens", "first_used_at", "first_used_at BIGINT"
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "last_used_ip_index",
            "last_used_ip_index TEXT",
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "last_used_ip_encrypted",
            "last_used_ip_encrypted TEXT",
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "last_used_user_agent",
            "last_used_user_agent TEXT",
        )
        _add_column_if_missing(
            db, "auth_api_access_tokens", "last_used_path", "last_used_path TEXT"
        )
        _add_column_if_missing(
            db, "auth_api_access_tokens", "expires_at", "expires_at BIGINT"
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "scope_mode",
            "scope_mode TEXT NOT NULL DEFAULT 'none'",
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "use_count_total",
            "use_count_total INTEGER NOT NULL DEFAULT 0",
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_api_access_token_scopes (
                id BIGINT PRIMARY KEY,
                token_id BIGINT NOT NULL,
                scope_type TEXT NOT NULL,
                value TEXT NOT NULL,
                created_by BIGINT,
                created_at BIGINT NOT NULL,
                UNIQUE(token_id, scope_type, value)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_api_access_token_events (
                id BIGINT PRIMARY KEY,
                token_id BIGINT NOT NULL,
                used_at BIGINT NOT NULL,
                ip_index TEXT,
                ip_encrypted TEXT,
                method TEXT,
                path TEXT,
                user_agent TEXT,
                allowed INTEGER NOT NULL DEFAULT 1,
                scope_match INTEGER,
                reject_reason TEXT
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_expires ON auth_api_access_tokens(expires_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_scopes_token ON auth_api_access_token_scopes(token_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_events_token_time ON auth_api_access_token_events(token_id, used_at DESC)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_events_ip ON auth_api_access_token_events(ip_index)"
        )
    except Exception:
        pass


def down(db):
    if db.type == "postgres":
        try:
            db.execute("DROP TABLE IF EXISTS auth_api_access_token_events")
            db.execute("DROP TABLE IF EXISTS auth_api_access_token_scopes")
        except Exception:
            pass
