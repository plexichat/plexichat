def up(db):
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_api_access_tokens (
                id BIGINT PRIMARY KEY,
                name TEXT,
                token_index TEXT UNIQUE NOT NULL,
                token_encrypted TEXT NOT NULL,
                created_by BIGINT,
                created_at BIGINT NOT NULL,
                last_used_at BIGINT,
                revoked INTEGER DEFAULT 0,
                revoked_at BIGINT,
                revoked_by BIGINT
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_index ON auth_api_access_tokens(token_index)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_revoked ON auth_api_access_tokens(revoked)"
        )
    except Exception:
        pass


def down(db):
    if db.type == "postgres":
        try:
            db.execute("DROP TABLE IF EXISTS auth_api_access_tokens")
        except Exception:
            pass
