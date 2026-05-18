# SQLite to PostgreSQL Migration Guide

This guide provides a comprehensive walkthrough for system administrators migrating a Plexichat production instance from SQLite to PostgreSQL.

## Why Migrate?

While SQLite is excellent for small deployments, PostgreSQL is recommended for:
- **High Concurrency:** Handle more than 100 daily active users with concurrent writes.
- **Reliability:** Better crash recovery and data integrity for large datasets.
- **Performance:** Native full-text search (GIN indexes) and optimized query planning.
- **Observability:** Detailed connection pool monitoring and statistics.

---

## Critical: Safety First

Before starting the migration, you **MUST**:
1.  **Stop the Plexichat Services:** Ensure no data is written during the migration.
2.  **Backup your SQLite File:** Copy `data/plexichat.db` to a secure location.
3.  **Verify PostgreSQL Access:** Ensure your PostgreSQL user has `CREATE` and `SUPERUSER` (or owner) permissions on the target database to handle triggers and sequences.

---

## Migration Steps

### 1. Install PostgreSQL Driver
The migration utility requires the `psycopg2-binary` package.
```bash
pip install psycopg2-binary
```

### 2. Prepare PostgreSQL
Create an empty database on your PostgreSQL server.
```sql
CREATE DATABASE plexichat;
```

### 3. Configure Environment Variables
The migration utility uses environment variables to connect to PostgreSQL.
```bash
export POSTGRES_HOST=your_host
export POSTGRES_PORT=5432
export POSTGRES_USER=plexichat
export POSTGRES_PASSWORD=your_password
export POSTGRES_DBNAME=plexichat
```

### 4. Run the Migration Utility
Use the built-in `migrate_to_postgres` command. This will initialize the schema, clone data, and set up PG-specific search indices.
```bash
python -m src.core.migrations.cli migrate_to_postgres --sqlite-path data/plexichat.db
```

### 5. Verify the Migration
The utility will automatically perform a row-count verification. You should see:
```text
Verifying data integrity...
Migration SUCCESSFUL!
Total tables cloned: 32
Total rows cloned:   15420
```

---

## Post-Migration Tasks

### 1. Update Application Configuration
Update your `config/config.yaml` to point to the new PostgreSQL database permanently.

```yaml
database:
  type: postgres
  postgres:
    host: localhost
    port: 5432
    user: plexichat
    password: your_password
    dbname: plexichat
    sslmode: prefer
```

### 2. Restart Plexichat
Restart your systemd services or Docker containers.
```bash
systemctl restart plexichat-server plexichat-client
```

---

## How it Works (Under the Hood)

The `migrate_to_postgres` utility performs the following automated steps:
1.  **Status Check:** Ensures the SQLite database is healthy and fully migrated.
2.  **Schema Bootstrap:** Runs the application's migration system on PostgreSQL to create the "perfect" schema (using `BIGINT`, `SERIAL`, etc.).
3.  **Batch Cloning:** Copies data in 1000-row batches using parameterized queries for safety and performance.
4.  **Sequence Correction:** Resets PostgreSQL `SERIAL` sequences so new IDs don't collide with migrated data.
5.  **Search Initialization:** Automatically creates the PostgreSQL Full-Text Search triggers and GIN indexes required by the search module.

## Troubleshooting

### Checksum Mismatch
If you see a checksum mismatch warning, it means your migration files on disk differ from those used to build the SQLite DB. Verify your codebase version matches the one that created the SQLite database.

### Permission Denied (Triggers)
If the utility fails during "Module Initialization," ensure your PostgreSQL user has the `TRIGGER` privilege or is the owner of the tables.

### Missing SQLite Path
If your SQLite file is not at the default `data/plexichat.db`, use the `--sqlite-path` argument to specify its location.

---

**Related Documentation:**
- [Database Configuration](./configuration.md)
- [Migration Reference](./migration-reference.md)
- [Performance Optimization](./performance.md)
