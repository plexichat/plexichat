# Database migrations

This document describes the migration directory layout, schema policy, and file conventions for Plexichat database migrations.

## Overview

The migration directory is intentionally minimal during the current development phase. The initial schema migration creates the full canonical schema, so fresh installations are deterministic.

| Version | File | Purpose |
|---------|------|---------|
| `000` | `000_initial_schema.py` | Create the complete canonical schema from module schema definitions. |
| `001` | `001_selftest_noop.py` | No-op migration for self-tests that exercise apply and rollback behavior. |

The migration runner discovers only `000` and `001` in this directory. See `plexichat/src/core/migrations/manager.py:93` and `plexichat/src/core/migrations/validator.py:193`.

## Schema policy

Add schema changes to the module schema definitions that `000_initial_schema.py` imports. Do not add a numbered production migration for each schema adjustment yet.

This keeps the initial schema as the authoritative source of truth for new SQLite and PostgreSQL installations. The module schema definitions include artifact feature settings, authentication hardening columns and indexes, server public visibility, and OAuth token usage timestamps.

When the project needs to support upgrades for already-deployed databases, introduce a reviewed, ordered migration strategy instead of restoring deleted files ad hoc.

The authoritative schema creator is `plexichat/src/core/migrations/migrations/000_initial_schema.py:59`. It calls each module's `create_tables` function in dependency order.

## Naming and format

Name migration files with the pattern `NNN_descriptive_name.py`, where `NNN` is a zero-padded version number.

Each migration file must export:

*   `up(db)` — apply the migration.
*   `down(db)` — roll back the migration.

For irreversible migrations, you may omit `down()`, but you must declare `MIGRATION_METADATA` in the module docstring with `"irreversible": true`. The validator accepts a missing `down()` only in that case. See `plexichat/src/core/migrations/validator.py:280`.

Example for an irreversible migration:

```python
"""
Add search index.

MIGRATION_METADATA:
{"irreversible": true}
"""

def up(db):
    db.execute("CREATE INDEX ...")
```

Do not add a fake or destructive no-op `down()` function to satisfy validation.

The runner screens migration SQL before execution and blocks dangerous patterns such as `DROP DATABASE`, `DROP SCHEMA`, and `TRUNCATE migrations_history`. See `plexichat/src/core/migrations/runner.py:62`.

## Retired versions and stale cache

Versions `002`, `003`, and `004` were consolidated into migration `000` and their source files were deleted. See `RETIRED_MIGRATIONS` in `plexichat/src/core/migrations/validator.py:20`. History rows for those versions remain valid on upgraded databases, and integrity checks treat them as retired rather than missing. See `plexichat/src/core/migrations/manager.py:496`.

Compiled artifacts for retired versions can linger in `migrations/__pycache__/`, for example `002_artifact_feature_settings.cpython-313.pyc`. The loader reads only explicit `.py` paths, so these files never execute, but you should remove them to avoid confusion:

```bash
rm -rf src/core/migrations/migrations/__pycache__
```

The loader warns about stale artifacts at startup. See `plexichat/src/core/migrations/validator.py:50`.

The `create_migration` helper accounts for retired versions when it computes the next version number, so a new migration never reuses `002` through `004`.

## Rollback for the initial schema

Rolling back `000` is destructive: it drops all application tables listed in `ROLLBACK_TABLE_ALLOWLIST` at `plexichat/src/core/migrations/migrations/000_initial_schema.py:129`. Migration tracking tables are never dropped.

You must set the environment variable `MIGRATIONS_ALLOW_DESTRUCTIVE_ROLLBACK=1` before `down()` runs. Otherwise the rollback is refused. The drop order handles foreign key dependencies on SQLite, and uses `CASCADE` on PostgreSQL.

## Related code

*   `plexichat/src/core/migrations/manager.py` — discovery, ordering, and execution
*   `plexichat/src/core/migrations/runner.py` — transaction handling and SQL screening
*   `plexichat/src/core/migrations/validator.py` — file validation and version ordering
*   `plexichat/src/core/migrations/tracker.py` — history and locking
