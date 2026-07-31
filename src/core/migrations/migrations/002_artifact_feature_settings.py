"""Add per-server artifact feature settings.

This table was added to the canonical artifacts schema after migration 000 was
already deployed.  Keep the migration idempotent so both clean and upgraded
databases converge on the same schema.
"""


def _execute(db, statement: str) -> None:
    converted = (
        db.convert_schema(statement) if hasattr(db, "convert_schema") else statement
    )
    db.execute(converted)


def up(db):
    _execute(
        db,
        """CREATE TABLE IF NOT EXISTS server_artifact_feature_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            feature TEXT NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(server_id, feature, setting_key)
        )""",
    )
    _execute(
        db,
        "CREATE INDEX IF NOT EXISTS idx_server_feature_settings_server "
        "ON server_artifact_feature_settings(server_id)",
    )
    _execute(
        db,
        "CREATE INDEX IF NOT EXISTS idx_server_feature_settings_feature "
        "ON server_artifact_feature_settings(server_id, feature)",
    )


def down(db):
    _execute(db, "DROP INDEX IF EXISTS idx_server_feature_settings_feature")
    _execute(db, "DROP INDEX IF EXISTS idx_server_feature_settings_server")
    _execute(db, "DROP TABLE IF EXISTS server_artifact_feature_settings")
