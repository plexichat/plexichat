"""
MIGRATION UTILITY: migrate_to_postgres

This utility automates the migration of a Plexichat SQLite database to PostgreSQL.

## How it works:
1. Validates that the SQLite source is fully migrated and healthy.
2. Initializes the target PostgreSQL database with a native schema by running all migrations.
3. Clones data from SQLite to PostgreSQL table-by-table.
4. Corrects PostgreSQL sequences (SERIAL columns).
5. Initializes engine-specific features (e.g., PostgreSQL Full-Text Search triggers).
6. Verifies row counts to ensure data integrity.

## Prerequisites:
- A running PostgreSQL server.
- The PostgreSQL driver installed (`pip install psycopg2-binary`).
- Superuser or table owner permissions on the PostgreSQL database.

## Usage:
Set your POSTGRES environment variables (HOST, USER, PASSWORD, DBNAME) then run:
python -m src.core.migrations.cli migrate_to_postgres --sqlite-path data/plexichat.db
"""

## Migration Gap Notice

If you see a log line like:
```
Gap in applied migration versions: missing version between 036 and 038
```
this is **informational only** and does not block new migrations. Version 037 exists in the `migrations/` directory and is intentionally a no-op used by the self-test system to validate apply/rollback endpoints. The warning is emitted because the migration tracker detects non-contiguous version numbers; 037 is part of the release train and is applied normally.
