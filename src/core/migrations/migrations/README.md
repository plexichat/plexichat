# Database Migrations

The project currently keeps the migration directory intentionally minimal:

| Version | File | Purpose |
|---|---|---|
| `000` | `000_initial_schema.py` | Creates the complete canonical schema from the module schema definitions. |
| `001` | `001_selftest_noop.py` | No-op migration used only to exercise migration apply/rollback behavior in self-tests. |

## Schema policy

For the current development phase, schema changes must be reflected in the
module schema definitions imported by `000_initial_schema.py`. Do not add a
numbered production migration for each schema adjustment yet. This keeps fresh
SQLite and PostgreSQL installations deterministic and makes the initial schema
the authoritative source of truth.

The module schema definitions include the artifact feature settings table,
authentication hardening columns and indexes, server public visibility, and
OAuth token usage timestamps. The migration runner should therefore discover
only `000` and `001` in this directory.

## Naming and format

Migration files use the `NNN_descriptive_name.py` convention and export:

- `up(db)` to apply the migration
- `down(db)` to roll it back (the `001` no-op intentionally changes nothing)

Irreversible migrations may omit `down()` entirely, but must declare
`MIGRATION_METADATA` in their docstring with `"irreversible": true`; the
validator accepts a missing `down()` only for those. Do not ship fake or
destructive no-op `down()` functions to satisfy validation.

## Retired versions and stale __pycache__

Versions `002`, `003`, and `004` were consolidated into migration `000` and
their source files were intentionally deleted (see
`RETIRED_MIGRATIONS` in the migration package). Their history rows remain
valid on upgraded databases.

Their compiled artifacts can linger in `migrations/__pycache__/`
(e.g. `002_artifact_feature_settings.cpython-313.pyc`). The loader only ever
reads explicit `.py` paths, so these are never executed — but they should be
cleaned up to avoid confusion:

```
rm -rf src/core/migrations/migrations/__pycache__
```

`create_migration` accounts for retired versions when computing the next
version number, so a new migration will never reuse `002`–`004`.

When the project is ready to support upgrades for already-deployed databases,
introduce a reviewed, ordered migration strategy rather than restoring deleted
migration files ad hoc.
