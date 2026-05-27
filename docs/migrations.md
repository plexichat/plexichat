# Migration Management CLI

The Plexichat server includes a built-in migration management tool accessible via the main application entry point.

## Running Migrations

To apply all pending database migrations, run the following command from the project root:

```bash
python main.py --migrate-db
```

This command will:
1. Detect your configuration.
2. Connect to the database.
3. Display the current migration status (number of applied and pending migrations).
4. Run all pending migrations.
5. Exit with a status code indicating success (0) or failure (1).

## Idempotency and Safety

The migration system is designed to be idempotent:
- All migrations include checks (`IF EXISTS` or column existence verification) before altering or dropping columns.
- The `run_migrations` function handles transaction management automatically. If a migration fails, it will roll back and stop further execution.
- Migrations track their application state in a dedicated `schema_migrations` table.
- Duplicate migration attempts are safely ignored (already-applied migrations are skipped).

## Irreversible Migrations

Some migrations are marked as **irreversible** and cannot be rolled back via the `down()` function:

- **Migration 029** (Drop unencrypted columns): Drops original `description` and `topic` columns after encrypted data migration is verified.
- **Migration 027** (Migrate encrypted data): Transforms data between columns -- once run, original column data is no longer source of truth.

**To rollback an irreversible migration:**
1. Restore the database from a backup taken *before* the migration was applied.
2. Manually remove the migration record from the `schema_migrations` table.
3. Re-apply any dependent migrations that reference the restored schema.

**Emergency override (not recommended):**
If a migration must be skipped (e.g., it blocks startup due to a schema mismatch), you can manually delete its record from `schema_migrations` and the system will skip it. However, this may cause data inconsistencies -- always prefer a proper backup restore.

## Dry-Run Mode

The migration system supports a dry-run mode for validation before making changes:

```bash
# Check migration status only (no changes applied)
python main.py --migrate-db --dry-run
```

In dry-run mode:
- The system displays which migrations would be applied.
- No schema or data changes are executed.
- Returns exit code 0 if all migrations are up-to-date, 1 if migrations are pending.

## Uptime Delay Configuration

For high-risk migrations (like encryption migrations), you can configure a minimum uptime delay to ensure the server has been running long enough before applying changes:

```yaml
migrations:
  minimum_uptime_hours: 24  # Wait 24 hours before applying high-risk migrations
```

This prevents automatic application of destructive changes on a freshly restarted server where the previous stable version hasn't been verified.

## Troubleshooting

If you encounter issues during migration:
- **Check the logs**: Detailed output is available in the `logs/` directory.
- **Resume after failure**: Run `python main.py --migrate-db` again -- already-applied migrations are skipped, and pending migrations resume from where they left off.
- **Missing columns/Indexes**: If a migration fails because a column or index already exists (or doesn't exist), the migration scripts use `IF EXISTS` / `IF NOT EXISTS` clauses where possible. If you encounter an unhandled case, check whether the migration was partially applied.
- **Manual intervention**: For SQLite databases, column drops require table recreation. If a migration fails mid-recreation, you may need to manually recover the `_new` table or restore from backup.
- **PostgreSQL specifics**: Column drops use `ALTER TABLE ... DROP COLUMN IF EXISTS`. Index recreation is automatic (indexes are not dropped by column drops).

## Best Practices

1. **Always backup before running migrations** -- especially for irreversible migrations.
2. **Test migrations in a staging environment first** before applying to production.
3. **Run migrations during maintenance windows** for large or high-risk migrations.
4. **Monitor logs** after migration to ensure data integrity.
5. **Do not skip migrations** unless absolutely necessary and you understand the schema implications.