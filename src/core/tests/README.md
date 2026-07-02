# Core Tests

Unit tests for core module components.

## Test Files

### `test_migrations.py`
Tests for the database migration system:

- `test_calculate_checksum()` - Verifies SHA-256 checksum computation on migration file content
- `test_migration_manager_init()` - Validates MigrationManager construction and migrations directory setup
- `test_get_pending_migrations()` - Tests discovery of unapplied migration files
- `test_apply_all_pending_success()` - Tests successful sequential application of migrations
- `test_validate_integrity_mismatch()` - Tests detection of checksum mismatches (tampered migration files)

Uses pytest fixtures for mock database connections and temporary migration file directories.
