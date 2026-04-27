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
