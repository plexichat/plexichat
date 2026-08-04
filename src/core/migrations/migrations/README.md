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

When the project is ready to support upgrades for already-deployed databases,
introduce a reviewed, ordered migration strategy rather than restoring deleted
migration files ad hoc.
