# Migrate a SQLite database to PostgreSQL

This document describes how to migrate a Plexichat SQLite database to PostgreSQL using the `migrate_to_postgres` utility.

## Overview

The utility clones all data from a SQLite source to a PostgreSQL target. It validates the source, creates the target schema, copies data table by table, fixes sequences, initializes PostgreSQL-specific features, and verifies row counts.

## How it works

The utility runs these steps in order:

1.  Validates that the SQLite source is fully migrated and healthy with no pending or failed migrations. See `plexichat/src/core/migrations/cloner.py:66`.
2.  Initializes the target PostgreSQL database with the native schema by running all migrations. See `plexichat/src/core/migrations/manager.py:262`.
3.  Clones data from SQLite to PostgreSQL table by table inside a per-table transaction. See `plexichat/src/core/migrations/cloner.py:132`.
4.  Resets PostgreSQL sequences for `SERIAL` columns so new inserts continue from the correct value.
5.  Initializes engine-specific features such as PostgreSQL full-text search triggers. See `plexichat/src/core/migrations/cloner.py:320`.
6.  Verifies row counts and content hashes to ensure data integrity. See `plexichat/src/core/migrations/cloner.py:384`.

If cloning fails mid-table, the utility rolls back that table so the target is not left empty or partial.

## Before you begin

You need:

*   A running PostgreSQL server.
*   The PostgreSQL driver: `pip install psycopg2-binary`.
*   Superuser or table-owner permissions on the target database, so the utility can set `session_replication_role` and reset sequences. If you run without superuser, cloning continues with foreign key checks active.

## Run the migration

1.  Set your PostgreSQL environment variables:

    ```bash
    export POSTGRES_HOST=localhost
    export POSTGRES_USER=plexichat
    export POSTGRES_PASSWORD=secret
    export POSTGRES_DBNAME=plexichat
    ```

2.  Run the migrator:

    ```bash
    python -m src.core.migrations.cli migrate_to_postgres --sqlite-path data/plexichat.db
    ```

The utility refuses to run if the source and target resolve to the same database. This prevents accidental deletion of production data. See `plexichat/src/core/migrations/cloner.py:51`.

## Handle migration gaps

If you see a log line such as:

```
Gap in applied migration versions: missing version between 036 and 038
```

This message is informational and does not block new migrations. Version `037` exists in the `migrations/` directory as an intentional no-op for self-tests that validate apply and rollback behavior. The tracker detects non-contiguous version numbers and emits this warning, but version `037` is part of the release train and applies normally. See `plexichat/src/core/migrations/validator.py:126`.

## Related code

*   `plexichat/src/core/migrations/cloner.py` — `DataCloner` implementation
*   `plexichat/src/core/migrations/cli.py` — command-line entry point
*   `plexichat/src/core/migrations/manager.py` — migration discovery and execution
*   `plexichat/src/core/migrations/tracker.py` — migration history tracking
