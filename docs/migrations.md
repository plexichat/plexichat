# Migration management

Plexichat tracks database schema changes with a built-in migration system. The system applies migrations in order, records their status, and supports dry runs, validation, and guarded rollbacks.

## Before you begin

You need a running database and a valid configuration file. Migrations run automatically on startup when `database.migrations.auto_migrate` is `true` (the default). For manual control, disable auto-migration and run the CLI.

## Run migrations

To apply all pending migrations, run the following command from the project root:

```bash
python main.py migrate-db
```

This command does the following:

1.  Loads configuration from `config/config.yaml` (or the path you pass with `--config`).
2.  Connects to the database.
3.  Prints the current status (number of applied and pending migrations).
4.  Applies all pending migrations in version order.
5.  Exits with status `0` on success or `1` on failure.

Alternatively, you can use the module CLI:

```bash
python -m src.core.migrations.cli apply_migrations
python -m src.core.migrations.cli list_migrations
python -m src.core.migrations.cli validate_migrations
```

## How tracking works

The system records migration state in the database, not in the filesystem.

| Table | Purpose |
|---|---|
| `migrations_history` | One row per migration version with status (`completed`, `running`, `failed`, `rolled_back`), checksum, timing, and metadata. |
| `migration_logs` | Per-migration log entries (`level`, `message`, `timestamp`). |
| `migration_uptime` | Uptime sessions used to enforce the irreversible-migration delay. A new session starts once per process per database. |
| `emergency_override_tokens` | Single-use tokens that bypass the irreversible delay. Tokens are stored as SHA-256 hashes. |
| `migration_lock` | Advisory lock that prevents concurrent migration runs. |

The authoritative migration files live in `src/core/migrations/migrations/`:

*   `000_initial_schema.py` — Creates all tables by calling each domain `create_tables(db)` function. See [Migration reference](migration-reference.md).
*   `001_selftest_noop.py` — No-op migration that the self-test suite uses to verify apply and rollback.

Duplicate runs are safe. The manager skips already-applied versions and resumes from the first pending migration.

## Idempotency and safety

*   Schema creators use `CREATE TABLE IF NOT EXISTS` and the migration runner validates checksums. If a file changes after it was recorded, the run is blocked unless you consume a valid emergency override token (`EMERGENCY_MIGRATION_OVERRIDE`).
*   `MigrationManager.apply_all_pending()` acquires the migration lock before running. A second concurrent run fails with a lock error.
*   If a migration fails, the system records `failed` status, invalidates the cached status, and stops. Fix the cause and re-run `python main.py migrate-db` to resume.
*   `down()` rollbacks are only available for migrations that implement `down(db)`. Migration `000` rollback is destructive and refused unless `MIGRATIONS_ALLOW_DESTRUCTIVE_ROLLBACK=1` is set.

## Irreversible migrations

A migration can declare itself irreversible in its docstring metadata:

```python
"""
MIGRATION_METADATA:
{"irreversible": true, "depends_on": ["000"]}
"""
```

For irreversible migrations, `MigrationTracker.can_run_irreversible_migration()` enforces an uptime gate:

*   If the migration declares `depends_on`, uptime is measured since the last dependency was applied.
*   If it declares no dependencies, uptime is measured since the most recently completed migration.
*   If there is no completed migration, the run is refused.

The required delay comes from `database.migrations.irreversible_migration_delay_days` (default `7` days). The tracker sums `migration_uptime.uptime_seconds` for sessions that started at or after the reference timestamp and adds the live elapsed time of the current session.

To bypass the delay in an emergency, generate a single-use token from the admin API (`POST /api/v1/admin/migrations/emergency-override`) and set it as `EMERGENCY_MIGRATION_OVERRIDE` in the environment. The token is consumed on first successful validation and cannot be replayed.

To recover from an irreversible migration that should not have run:

1.  Restore the database from a backup taken before the migration.
2.  Verify the restored state with `python -m src.core.migrations.cli validate_migrations`.
3.  Re-apply any expected pending migrations.

Do not manually delete rows from `migrations_history` to skip a migration. This causes checksum and ordering errors.

## Dry-run mode

Validate migrations without changing the database or the tracking tables:

```bash
python main.py migrate-db --dry-run
```

In dry-run mode:

*   The system lists which migrations would be applied.
*   No schema changes are committed and no tracker rows are written.
*   The process exits with `0` on success.

Use `--dry-run` in CI and before production deploys.

## Configuration

Configure migrations under `database.migrations` in `config.yaml`:

```yaml
database:
  migrations:
    auto_migrate: true
    migration_dir: "~/.plexichat/migrations"
    irreversible_migration_delay_days: 7
```

| Field | Default | Description |
|---|---|---|
| `auto_migrate` | `true` | Apply pending migrations on startup. Set to `false` for blue-green deploys where you run migrations manually. |
| `migration_dir` | `~/.plexichat/migrations` | Directory that contains migration files. The manager creates it if it does not exist. |
| `irreversible_migration_delay_days` | `7` | Required uptime before an irreversible migration can run. The admin API converts this to hours (`days * 24`). |

Do not use `migrations.minimum_uptime_hours`. The correct key is `database.migrations.irreversible_migration_delay_days`.

## Check status and logs

*   CLI: `python -m src.core.migrations.cli list_migrations` and `validate_migrations`.
*   Admin API: `GET /api/v1/admin/migrations` and `GET /api/v1/admin/migrations/status` (requires `admin.migrations.execute` and host restriction).
*   Python: `from src.core.migrations import get_status; get_status(db)`.

Each migration's logs are available through `MigrationTracker.get_migration_logs(version)` and the admin detail endpoint `GET /api/v1/admin/migrations/{version}`.

## Troubleshooting

*   **Logs** — Check `logs/` and the `migration_logs` table. The CLI also prints a summary with `applied_count` and `failed_count`.
*   **Lock errors** — `Could not acquire migration lock` means another migration is running. Wait for it to finish or clear a stale lock.
*   **Checksum mismatch** — The file was modified after it was recorded. Restore the original file or consume a valid emergency override token. See `src/core/migrations/manager.py:539`.
*   **SQLite column drops** — Handled with table recreation in the migration. If a run fails mid-recreation, restore from backup.
*   **PostgreSQL column drops** — Use `ALTER TABLE ... DROP COLUMN IF EXISTS`. Indexes are recreated automatically on SQLite only where needed.
*   **Status looks stale** — The manager caches `get_migration_status()` for 15 seconds with a per-database key. The cache is invalidated after apply or rollback. See `src/core/migrations/manager.py:435`.

## Best practices

*   Back up the database before running migrations, especially before any irreversible migration. For example, `docker exec plexichat-backup-1 /scripts/backup.sh run`.
*   Test migrations in a staging environment that mirrors production.
*   Run migrations during a maintenance window for high-risk changes.
*   Monitor logs after migration and run `validate_migrations` to confirm checksums.

## Related documentation

*   [Migration reference](migration-reference.md) — Authoritative list of migrations and schema modules.
*   [Database configuration](deployment/configuration/config-database.md) — Connection pool and monitoring.
*   [Admin migrations API](api/templates/admin/migrations.html) — UI for listing and running migrations.
