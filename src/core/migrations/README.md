"""
Migration System Documentation

## Overview

The migration system provides a robust, traceable way to manage database schema changes
across the plexichat application. It includes:

- **Automatic Tracking**: All migrations are recorded in the `migrations_history` table
- **Checksum Validation**: SHA256 checksums detect tampered migration files
- **Rollback Support**: Migrations can be rolled back if a `down()` function is provided
- **Dry-Run Mode**: Test migrations without making actual changes
- **Transaction Safety**: Each migration runs in its own transaction
- **Multiple Database Support**: Works with both SQLite and PostgreSQL
- **Version Control**: Prevents the same migration from running twice

## Architecture

```
migrations/
  __init__.py          # Public API (run_migrations, rollback, get_status)
  manager.py           # Main orchestrator (MigrationManager class)
  runner.py            # Migration execution (MigrationRunner class)
  tracker.py           # Database tracking (MigrationTracker class)
  validator.py         # Validation utilities
  schema.py            # Schema definition for migrations_history table
  cli.py               # Command-line interface
  migrations/          # Individual migration files
    __init__.py
    001_initial_example.py
    002_your_migration.py
    ...
```

## Creating a New Migration

### 1. Generate Migration File

Use the CLI to create a new migration template:

```bash
python -m src.core.migrations.cli create_migration "add users table"
```

This creates a file like `plexichat/src/core/migrations/migrations/002_add_users_table.py`

### 2. Implement Migration Logic

Edit the generated migration file and implement the `up()` function:

```python
def up(db):
    """Apply the migration."""
    db.execute('''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(255) NOT NULL UNIQUE,
            email VARCHAR(255) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    db.execute('''
        CREATE INDEX idx_users_username ON users(username)
    ''')
```

### 3. Implement Rollback (Optional but Recommended)

Implement the `down()` function to enable rollbacks:

```python
def down(db):
    """Rollback the migration."""
    db.execute('DROP TABLE IF EXISTS users')
```

## Migration File Naming Convention

Migration files must follow the naming pattern:

```
NNN_description.py

Where:
  NNN = Zero-padded version number (001, 002, 003, etc.)
  description = Kebab-case description of the migration
```

Examples:
- `001_initial_schema.py`
- `002_add_users_table.py`
- `003_add_authentication.py`
- `004_add_session_tracking.py`

## Running Migrations

### Automatic (Application Startup)

Migrations run automatically when the application starts:

```python
# In plexichat/main.py
from src.core.migrations import run_migrations

run_migrations(self.db)
```

The baseline schema is created by migration `000_initial_schema`. Module setup no
longer creates tables, so run migrations before using managers or tests.

### Manual

```python
from src.core.migrations import run_migrations

result = run_migrations(db)
print(f"Applied {result['applied_count']} migrations")
```

### Dry-Run Mode

Test migrations without making actual changes:

```python
result = run_migrations(db, dry_run=True)
# No changes are committed to the database
```

### Command-Line Interface

```bash
# List all migrations and their status
python -m src.core.migrations.cli list_migrations

# Apply pending migrations
python -m src.core.migrations.cli apply_migrations

# Test migrations without applying
python -m src.core.migrations.cli apply_migrations --dry-run

# Rollback a specific migration
python -m src.core.migrations.cli rollback_migration 002

# Check migration integrity
python -m src.core.migrations.cli validate_migrations
```

## Migration Status Tracking

The `migrations_history` table tracks:

- **version**: Migration version (e.g., '001')
- **name**: Human-readable name
- **applied_at**: When the migration was applied
- **applied_by**: User or system that applied it
- **execution_time_ms**: How long the migration took
- **checksum**: SHA256 hash of the migration file (detects tampering)
- **status**: One of 'completed', 'running', 'failed', or 'rolled_back'
- **error_message**: Error details if migration failed
- **rollback_sql**: Indicator that rollback is available

## Best Practices

### 1. Keep Migrations Small and Focused

Good:
```python
# 002_create_users_table.py
def up(db):
    db.execute('CREATE TABLE users (...)')
```

Bad:
```python
# 002_create_all_tables.py
def up(db):
    db.execute('CREATE TABLE users (...)')
    db.execute('CREATE TABLE posts (...)')
    db.execute('CREATE TABLE comments (...)')
    # etc...
```

### 2. Use Parameterized Queries

Safe:
```python
db.execute(
    'INSERT INTO users (username, email) VALUES (?, ?)',
    (username, email)
)
```

Unsafe:
```python
db.execute(f'INSERT INTO users VALUES ({username}, {email})')
```

### 3. Make Migrations Idempotent

Good:
```python
db.execute('CREATE TABLE IF NOT EXISTS users (...)')
db.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
```

Bad:
```python
db.execute('CREATE TABLE users (...)')  # Fails if table exists
```

### 4. Always Implement down()

Every migration should have a corresponding `down()` function:

```python
def up(db):
    db.execute('ALTER TABLE users ADD COLUMN active BOOLEAN DEFAULT 1')

def down(db):
    db.execute('ALTER TABLE users DROP COLUMN active')
```

### 5. Test in Development First

Test migrations locally before deploying:

```bash
# Test in dry-run mode first
python -m src.core.migrations.cli apply_migrations --dry-run

# Then apply for real
python -m src.core.migrations.cli apply_migrations

# If needed, rollback
python -m src.core.migrations.cli rollback_migration 003
```

### 6. Handle Data Transformations Carefully

When changing data structure, include data migration logic:

```python
def up(db):
    # Add new column with default value
    db.execute('ALTER TABLE users ADD COLUMN active BOOLEAN DEFAULT 1')
    
    # Set active to 0 for deleted users
    db.execute('UPDATE users SET active = 0 WHERE deleted_at IS NOT NULL')
    
    # Add constraint
    db.execute('ALTER TABLE users ADD CHECK (active IN (0, 1))')
```

## Troubleshooting

### Migration File Not Found

```
Error: Migration file for version 003 not found
```

**Solution**: Check that the migration file exists in the `migrations/` directory
with the correct naming convention.

### Checksum Mismatch

```
Error: Checksum mismatch for migration 002
Migration file may have been tampered with.
```

**Solution**: Do not modify applied migration files. If you need to make changes,
create a new migration instead.

### Transaction Rollback

If a migration fails:

1. The transaction is automatically rolled back
2. The migration status is recorded as 'failed' with error details
3. No partial schema changes are left in the database
4. Fix the migration file and try again

### Rollback Not Available

```
Error: Migration 002 has no down() function, rollback not available
```

**Solution**: Implement the `down()` function in the migration file if rollback
is needed.

## Getting Status

Check the current migration status:

```python
from src.core.migrations import get_status

status = get_status(db)
print(f"Applied: {status['applied_count']}")
print(f"Pending: {status['pending_count']}")
print(f"Failed: {status['failed_count']}")

for version in status['applied_migrations']:
    print(f"  - {version}")
```

## API Reference

### run_migrations(db, dry_run=False)

Apply all pending migrations.

**Parameters:**
- `db`: Database instance
- `dry_run`: If True, execute without committing (default: False)

**Returns:** Dictionary with migration results

**Example:**
```python
result = run_migrations(db)
if result['success']:
    print(f"Applied {result['applied_count']} migrations")
```

### rollback(db, version)

Rollback a specific migration.

**Parameters:**
- `db`: Database instance
- `version`: Migration version to rollback (e.g., '001')

**Returns:** Dictionary with rollback result

**Example:**
```python
result = rollback(db, version='002')
if result['success']:
    print(f"Rolled back migration {result['version']}")
```

### get_status(db)

Get current migration status.

**Parameters:**
- `db`: Database instance

**Returns:** Dictionary with applied/pending/failed counts and lists

**Example:**
```python
status = get_status(db)
for record in status['all_records']:
    print(f"{record['version']}: {record['status']}")
```

## Common Migration Patterns

### Create a Table

```python
def up(db):
    db.execute('''
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            price DECIMAL(10, 2),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

def down(db):
    db.execute('DROP TABLE products')
```

### Add a Column

```python
def up(db):
    db.execute('ALTER TABLE users ADD COLUMN bio TEXT')

def down(db):
    db.execute('ALTER TABLE users DROP COLUMN bio')
```

### Create an Index

```python
def up(db):
    db.execute('CREATE INDEX idx_users_email ON users(email)')

def down(db):
    db.execute('DROP INDEX idx_users_email')
```

### Insert Data

```python
def up(db):
    db.execute(
        'INSERT INTO roles (name, description) VALUES (?, ?)',
        ('admin', 'Administrator role')
    )

def down(db):
    db.execute('DELETE FROM roles WHERE name = ?', ('admin',))
```

### Rename a Column (PostgreSQL)

```python
def up(db):
    if db.engine == 'postgresql':
        db.execute('ALTER TABLE users RENAME COLUMN user_name TO username')
    else:  # SQLite
        db.execute('ALTER TABLE users RENAME user_name TO username')

def down(db):
    if db.engine == 'postgresql':
        db.execute('ALTER TABLE users RENAME COLUMN username TO user_name')
    else:  # SQLite
        db.execute('ALTER TABLE users RENAME username TO user_name')
```

## Safety Considerations

### Pre-Migration Checks

1. Test migrations in development/staging first
2. Use dry-run mode to review changes
3. Backup database before applying to production
4. Review migration file checksums for tampering

### Post-Migration Verification

1. Check migration status: `python -m src.core.migrations.cli list_migrations`
2. Validate integrity: `python -m src.core.migrations.cli validate_migrations`
3. Monitor application logs for any issues

### Rollback Strategy

1. Keep rollback (`down()`) functions up-to-date
2. Test rollback in development before production
3. Have database backups available for emergencies
4. Document any data loss risks in migration comments

## Integration with Application

The migration system integrates automatically with the plexichat application:

```python
# In plexichat/main.py
class Plexichat:
    def __init__(self):
        self.db = Database()
        # Migrations run automatically before connecting
        from src.core.migrations import run_migrations
        run_migrations(self.db)
        self.db.connect()
```

This ensures that migrations are applied before any application code accesses
the database, preventing schema mismatch errors.

## Support

For issues or questions about migrations:

1. Check the Troubleshooting section above
2. Review migration logs in application output
3. Validate migration files: `python -m src.core.migrations.cli validate_migrations`
4. Check `migrations_history` table for error details
"""
