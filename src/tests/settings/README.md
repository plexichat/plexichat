# Settings unit tests

This directory contains unit tests for the user settings module. The tests verify
that settings operations are correct, performant, and secure.

## Test files

| File | Description |
|------|-------------|
| `test_settings.py` | CRUD operations, user isolation, limit enforcement, edge cases, and timestamps |
| `test_w6c_fixes.py` | Regression tests for key charset contracts, atomic bulk writes, and cache invalidation |
| `test_settings_bugfixes.py` | Bug-fix regression tests for per-user limits, targeted queries, and schema cleanup |

## What the tests cover

### CRUD operations

`set`, `get`, `delete`, `get_all`, `get_count`, and list operations on
per-user settings.

### User isolation

Settings are scoped per user and never leak across accounts.

### Limit enforcement

Key length, value length, reserved key prefixes, and per-user setting caps are
enforced atomically.

### Key charset contract

Keys must match `[a-z0-9_-]`. Invalid keys and reserved namespaces (`_`, `__`)
are rejected.

### Atomic bulk writes

`set_many` commits all-or-nothing with no partial state on failure.

### Cache invalidation

User-scoped cache eviction does not affect other users. Invalidation helpers
build keys that match the cached decorator layout.

### Timestamps

`created_at` is set on creation. `updated_at` changes on update while
`created_at` remains stable.

### Edge cases

Empty values, special characters, Unicode, and legacy junk key filtering.

## Running the tests

```bash
# Run all settings tests
pytest src/tests/settings/ -v

# Run a specific test class
pytest src/tests/settings/test_settings.py::TestSettingsBasicOperations -v

# Run with coverage
pytest src/tests/settings/ --cov=src.core.settings -v
```

## Key classes and fixtures

| Class | Description |
|-------|-------------|
| `TestSettingsBasicOperations` | Basic set, get, delete, and list operations |
| `TestSettingsUserIsolation` | Cross-user isolation guarantees |
| `TestSettingsLimits` | Limit enforcement for key length, value length, and reserved prefixes |
| `TestSettingsEdgeCases` | Empty values, special characters, Unicode, and settings list retrieval |
| `TestSettingsTimestamps` | Creation and update timestamp behavior |
| `TestA1KeyContract` | Key charset validation and reserved namespace rules |
| `TestA2AtomicBulk` | Atomic bulk write behavior and rollback semantics |
| `TestB3ScopedInvalidation` | User-scoped cache invalidation patterns |
| `TestSet2LimitEnforcement` | Per-user limit is enforced exactly with no off-by-one |
| `TestSet7TargetedGet` | Single-setting retrieval uses a targeted query |
| `TestSet9DeadColumn` | The unused `value_encrypted` column is removed from the schema |

## Related source code

- `src/core/settings/manager.py` — `SettingsManager` class
- `src/core/settings/schema.py` — database schema definitions
- `src/core/settings/exceptions.py` — settings-specific exceptions
- `src/api/routes/settings.py` — API routes and cache invalidation helpers
