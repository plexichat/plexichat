# Core unit tests

This directory contains unit tests for the core module components.

## Test file

### `test_migrations.py`

Tests for the database migration system.

## What the tests cover

- **Checksum computation** — `calculate_checksum` produces a 64-character SHA-256
  hash that is deterministic for identical input.
- **Manager initialization** — `MigrationManager` stores the database reference
  and creates the migrations directory if it does not exist.
- **Pending migration discovery** — `get_pending_migrations` returns only
  migration files whose version has not been marked as applied in the tracker.
- **Sequential application** — `apply_all_pending` executes pending migrations in
  order, records results, and reports the applied count.
- **Integrity validation** — `validate_migration_integrity` detects checksum
  mismatches between the on-disk file and the tracker record, indicating a
  tampered migration.

## Fixtures

| Fixture | Description |
|---------|-------------|
| `mock_db` | A mocked database object with `fetch_all` and `fetch_one` stubs |
| `temp_migrations_dir` | A temporary directory for migration files, cleaned up after the test |

## Related source code

- `src/core/migrations/manager.py` — `MigrationManager` and `Migration` dataclass
- `src/core/migrations/validator.py` — `calculate_checksum`, `validate_migration_order`, and `get_migration_files`
