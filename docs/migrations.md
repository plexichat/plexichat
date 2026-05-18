# Database Migrations Guide

This guide explains the Plexichat database migration system for system administrators deploying and maintaining Plexichat instances.

## Overview

Plexichat uses a custom migration system to manage database schema changes. Migrations are versioned Python files in `src/core/migrations/migrations/` that define schema changes using a simple `up()` and optional `down()` pattern.

### Key Concepts

- **Migration Version**: Numeric identifier (e.g., `001`, `025`) that orders migrations
- **Reversible Migrations**: Can be rolled back using the `down()` function
- **Irreversible Migrations**: Cannot be rolled back - typically drop columns or tables
- **Dependencies**: Some migrations depend on others being applied first
- **Uptime-Based Delay**: Irreversible migrations require server uptime before execution
- **Emergency Override**: Temporary bypass for migration delays in emergencies

## Migration System Architecture

### Automatic Migration Execution

Migrations run automatically on server startup when:

1. The server starts
2. The migration tracker detects pending migrations
3. Each migration is applied in version order
4. Status is recorded in the `migrations_history` table

### Migration Metadata

Each migration file contains metadata in its docstring:

```python
"""
MIGRATION_METADATA:
{
    "irreversible": true,
    "depends_on": ["024"],
    "description": "Drops unencrypted columns after encryption verification",
    "risk_level": "high",
    "backup_required": true
}
"""
```

- `irreversible`: Whether the migration can be rolled back
- `depends_on`: List of migration versions that must be applied first
- `description`: Human-readable description of what the migration does
- `risk_level`: `low`, `medium`, or `high` - indicates potential impact
- `backup_required`: Whether a database backup is recommended before running

## Irreversible Migrations

### What Are Irreversible Migrations?

Irreversible migrations perform destructive operations that cannot be easily reversed, such as:

- Dropping database columns
- Dropping database tables
- Removing indexes that contain data

These migrations are marked with `"irreversible": true` in their metadata.

### Uptime-Based Delay Mechanism

To prevent accidental data loss, irreversible migrations are subject to a configurable delay period based on **server uptime** since their dependencies were applied.

#### Configuration

Set the delay period in your `config/config.yaml`:

```yaml
database:
  migrations:
    irreversible_migration_delay_days: 7  # Default: 7 days
```

#### How It Works

1. When a reversible migration (e.g., `024`) is applied, the system starts tracking uptime
2. The irreversible migration (e.g., `025`) depends on `024`
3. Before running `025`, the system checks if sufficient uptime has elapsed since `024` was applied
4. If not enough uptime has passed, the migration is blocked with a message showing remaining time

#### Why Uptime Instead of Real-Time?

Using uptime ensures the system has been stable and actively used for the configured period, reducing the risk of running irreversible operations on a system that may have issues.

### Running Irreversible Migrations

Irreversible migrations should be run through the **admin panel** at `/api/v1/admin/ui-migrations`:

1. Navigate to the Migrations tab in the admin panel
2. Find the irreversible migration in the pending list
3. Click "Run" (dry-run is recommended first)
4. Type the confirmation phrase: `THE DATABASE IS BACKED UP`
5. Confirm to execute

**Important**: Always ensure you have a recent database backup before running irreversible migrations.

## Emergency Override

In emergency situations where you cannot wait for the uptime delay, you can use the emergency override mechanism.

### Generating an Override Token

1. Navigate to the Migrations page in the admin panel
2. Click the "Emergency Override" link
3. Provide a reason for the emergency access
4. Set the expiration time (default 30 minutes, max 120 minutes)
5. Click "Generate Token"

### Using the Override Token

The admin panel will display a random token. Set this as an environment variable:

```bash
export EMERGENCY_MIGRATION_OVERRIDE=<generated_token>
```

Then restart the Plexichat server. The migration will now be executable regardless of uptime delay.

**Important**: 
- Tokens expire after the configured time
- Each token can only be used once
- Token usage is logged for audit purposes
- This should only be used in genuine emergencies

## Dry-Run Mode

Before running any migration, especially irreversible ones, use dry-run mode to test without making changes.

### Via Admin Panel

1. Navigate to the Migrations page
2. Click "Dry Run" instead of "Run" for the migration
3. Review the output to verify the migration would execute correctly

### Via CLI

```bash
python -m src.core.migrations.cli --dry-run
```

Dry-run mode:
- Executes the migration in a database transaction
- Rolls back the transaction at the end
- Shows what would happen without making permanent changes
- Useful for testing migration logic and timing

## Migration Logs

All migration activity is logged to the database in the `migration_logs` table for audit purposes.

### Viewing Logs

Via the admin panel:
1. Navigate to the Migrations page
2. Click "Details" for any migration
3. View the logs section showing all log entries for that migration

Logs include:
- Timestamp
- Log level (INFO, WARNING, ERROR)
- Log message

### Log Persistence

Logs are persisted in the database and:
- Survive server restarts
- Provide an audit trail of all migration activity
- Help diagnose migration issues
- Are automatically cleaned up when a migration is rolled back

## Admin Panel Migration Management

The admin panel provides a web interface for managing migrations at `/api/v1/admin/ui-migrations`.

### Features

- **Migration List**: View all migrations with status (applied, pending, failed)
- **Migration Details**: View metadata, dependencies, and logs for each migration
- **Run Migration**: Execute pending migrations with dry-run option
- **Rollback**: Rollback reversible migrations (if supported)
- **Status Indicators**: See which migrations are irreversible and can be run
- **Emergency Override**: Generate emergency tokens for bypassing delays

### Accessing the Admin Panel

1. Log in to the admin panel at `/api/v1/admin/login`
2. Navigate to `/api/v1/admin/ui-migrations`
3. Authenticate with admin credentials

## Migration Best Practices

### Before Running Migrations

1. **Always Backup**: Create a database backup before running any migration
2. **Test First**: Use dry-run mode to test the migration
3. **Check Dependencies**: Ensure all dependency migrations are applied
4. **Review Logs**: Check previous migration logs for any issues
5. **Monitor Uptime**: For irreversible migrations, ensure sufficient uptime has elapsed

### During Migration Execution

1. **Monitor Logs**: Watch the migration logs for errors or warnings
2. **Don't Interrupt**: Allow migrations to complete without stopping the server
3. **Check Status**: Verify the migration completed successfully in the admin panel
4. **Validate Data**: For encryption migrations, verify data can be decrypted

### After Migration Execution

1. **Verify Functionality**: Test the application to ensure everything works
2. **Check Logs**: Review migration logs for any warnings
3. **Monitor Performance**: Watch for any performance issues post-migration
4. **Document Changes**: Note any configuration or operational changes required

### For Irreversible Migrations

1. **Verify Backup**: Confirm your backup is valid and accessible
2. **Wait for Delay**: Ensure the uptime delay has elapsed (or use emergency override)
3. **Use Dry-Run**: Test the migration in dry-run mode first
4. **Type Confirmation**: Type the confirmation phrase carefully
5. **Monitor Closely**: Watch the migration execution and logs
6. **Have Rollback Plan**: Know how to restore from backup if needed

## Troubleshooting

### Migration Fails to Apply

**Symptoms**: Migration shows as "failed" in the admin panel

**Solutions**:
1. Check the migration logs for error messages
2. Verify database connectivity and permissions
3. Ensure dependency migrations are applied
4. Check for schema conflicts with existing data
5. Try running the migration again after fixing the issue

### Insufficient Uptime Error

**Symptoms**: Migration blocked with "Insufficient uptime" message

**Solutions**:
1. Wait for the configured uptime delay to elapse
2. Use emergency override if genuinely urgent
3. Check the uptime tracking is working correctly

### Encryption Validation Fails

**Symptoms**: Encryption migration fails validation

**Solutions**:
1. Verify encryption keys are correctly configured
2. Check encryption module is initialized
3. Ensure the encryption algorithm matches expectations
4. Review the validation error logs for specific issues

### Migration Stuck in "Running" State

**Symptoms**: Migration shows as "running" but not progressing

**Solutions**:
1. Check server logs for errors
2. Verify database is not locked
3. Check for long-running transactions
4. Restart the server to clear the migration lock
5. Manually update the migration status in the database if needed

## Migration Reference

### Current Irreversible Migrations

| Version | Name | Depends On | Description |
|---------|------|------------|-------------|
| 025 | Drop poll unencrypted columns | 024 | Drops unencrypted poll columns after encryption verification |
| 028 | Drop description unencrypted columns | 027 | Drops unencrypted description/topic columns after encryption verification |
| 031 | Drop internal notes unencrypted columns | 030 | Drops unencrypted internal notes columns after encryption verification |

### Migration Configuration

All migration-related configuration is in `config/config.yaml`:

```yaml
database:
  migrations:
    # Delay period for irreversible migrations (in days)
    irreversible_migration_delay_days: 7

encryption:
  # Enable encryption for descriptions, topics, etc.
  encrypt_descriptions: false
  encrypt_polls: false
  encrypt_internal_notes: false
```

## CLI Migration Commands

For advanced users, migrations can be managed via CLI:

```bash
# List pending migrations
python -m src.core.migrations.cli status

# Run all pending migrations
python -m src.core.migrations.cli migrate

# Migrate from SQLite to PostgreSQL
# See: docs/deployment/postgres-migration.md
python -m src.core.migrations.cli migrate_to_postgres --sqlite-path data/plexichat.db
```


## Security Considerations

### Migration Security

- Migration files should be reviewed before deployment
- Irreversible migrations require explicit confirmation
- Emergency override tokens are time-limited and single-use
- All migration activity is logged for audit
- Admin panel access requires authentication

### Database Access

- Ensure database credentials are securely stored
- Use least-privilege database accounts for the application
- Review migration SQL for security issues
- Test migrations in a staging environment first

## Support

For migration-related issues:

1. Check the migration logs in the admin panel
2. Review server logs for error messages
3. Consult the migration file for expected behavior
4. Check this documentation for troubleshooting steps
5. Contact Plexichat support with migration version and error details
