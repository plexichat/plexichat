# Database Migrations

Versioned migration scripts for the Plexichat database schema.

## Naming Convention

```
NNN_descriptive_name.py
```

- `NNN` -- Zero-padded sequential number (000, 001, 002, ...)
- `descriptive_name` -- Short snake_case description of the change

## Format

Each migration file must export:

| Function | Signature | Purpose |
|----------|-----------|---------|
| `up(db)` | `up(database.Database)` | Apply the migration |
| `down(db)` | `down(database.Database)` | Roll back the migration (optional but recommended) |

## Migration Index

| # | File | Description |
|---|------|-------------|
| 000 | `000_initial_schema.py` | Foundational schema -- creates all core tables via module schema definitions |
| 001 | `001_initial_example.py` | Template/reference migration |
| 002 | `002_legacy_transfer.py` | Legacy data transfer utilities |
| 003 | `003_add_totp_secret_encrypted.py` | Add encrypted TOTP secret column |
| 004 | `004_fix_user_settings_pk.py` | Fix primary key in user settings |
| 005 | `005_consolidated_schema_fixes.py` | Batch schema corrections |
| 006 | `006_add_msg_status_index.py` | Index for message status queries |
| 007 | `007_add_username_blacklist.py` | Username blacklist table |
| 008 | `008_harden_admin_security.py` | Admin security hardening |
| 009 | `009_add_db_metrics_to_telemetry.py` | Database metrics in telemetry |
| 010-016 | Various | Feature expansions (internal notes, content indexing, applied roles, timeouts, API tokens, max reactions) |
| 017 | `017_add_account_deletion_support.py` | Account deletion workflow support |
| 018 | `018_harden_admin_2fa.py` | Admin 2FA enforcement |
| 019 | `019_feature_expansion.py` | General feature expansion |
| 020-021 | `020-021_*` | Fix last_chat BIGINT columns for Postgres/SQLite |
| 022-032 | `022-032_*` | Encryption configuration -- adds encrypted columns, migrates data, drops unencrypted columns (auth, polls, descriptions, internal notes) |
| 033 | `033_admin_rbac_system.py` | Admin role-based access control |
| 034 | `034_admin_notes_versioning.py` | Versioned admin notes |
| 035 | `035_plexijoin_federation_system.py` | Federation schema for PlexiJoin |
| 036 | `036_fix_thread_threads_columns.py` | Threads table column fixes |

## Migration Runner

Migrations are executed by the migration system in `plexichat/src/core/migrations/`.
