# Migrate from SQLite to PostgreSQL

This guide describes how to migrate a Plexichat deployment from SQLite to PostgreSQL 18.

## Why migrate

Use PostgreSQL for production deployments that need:

- **High concurrency** — Handles more than 100 daily active users with concurrent writes.
- **Reliability** — Better crash recovery and data integrity for large datasets.
- **Performance** — Native full-text search with GIN indexes and optimized query planning.
- **Observability** — Detailed connection pool monitoring and statistics.

SQLite remains suitable for development and small deployments.

## Before you begin

Complete these steps before you migrate:

1. Stop the Plexichat services so no data is written during migration.
2. Back up your SQLite file. By default, this is `~/.plexichat/data/plexichat.db` or `data/plexichat.db`.
3. Verify that your PostgreSQL user has `CREATE` and owner or `TRIGGER` privileges on the target database. The migration creates triggers and GIN indexes for search.

The Docker Compose file uses `postgres:18-alpine`. Use the same major version for the target server.

## Migration steps

### 1. Install the PostgreSQL driver

The migration utility requires `psycopg2-binary`:

```bash
pip install psycopg2-binary
```

### 2. Prepare PostgreSQL

Create an empty database:

```sql
CREATE DATABASE plexichat;
```

### 3. Configure environment variables

The migration utility reads PostgreSQL connection details from environment variables or `DATABASE_URL`. Set one of the following:

**Option A — Individual variables:**

```bash
export POSTGRES_HOST=your_host
export POSTGRES_PORT=5432
export POSTGRES_USER=plexichat
export POSTGRES_PASSWORD=your_password
export POSTGRES_DBNAME=plexichat
export POSTGRES_SSLMODE=prefer
```

**Option B — Connection URL:**

```bash
export DATABASE_URL=postgresql://plexichat:your_password@your_host:5432/plexichat
```

The CLI prefers `DATABASE_URL` when it is set to a `postgres://` or `postgresql://` URL.

### 4. Run the migration utility

Use the built-in CLI. This command initializes the PostgreSQL schema, clones data in 1000-row batches, resets sequences, and creates search indexes:

```bash
python -m src.core.migrations.cli migrate_to_postgres --sqlite-path data/plexichat.db
```

To preview the schema initialization without cloning data:

```bash
python -m src.core.migrations.cli migrate_to_postgres --sqlite-path data/plexichat.db --dry-run
```

For the `python main.py` wrapper, use:

```bash
python main.py migrate-db --dry-run   # preview
python main.py migrate-db             # apply pending migrations
```

### 5. Verify the migration

The utility verifies row counts automatically. A successful run prints output similar to:

```text
Verifying data integrity...
Migration SUCCESSFUL!
Total tables cloned: 32
Total rows cloned:   15420
```

If row counts mismatch, review the warning and compare source and target tables before switching.

## Post-migration tasks

### 1. Update application configuration

Update your `config/config.yaml` or `~/.plexichat/config/config.yaml` to use PostgreSQL permanently:

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

Alternatively, keep using environment variable interpolation as in `plexichat/src/config_defaults.py:73`:

```yaml
database:
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD}"
```

### 2. Restart Plexichat

```bash
# Docker Compose
docker compose up -d

# systemd
systemctl restart plexichat-server plexichat-client
```

Verify health:

```bash
curl -f http://localhost:8000/api/v1/ready
curl -f http://localhost:8000/api/v1/health
```

## How it works

The `migrate_to_postgres` command performs these steps:

1. **Status check** — Ensures the SQLite database is healthy and all migrations are applied.
2. **Schema bootstrap** — Runs the application's migration system on PostgreSQL to create the canonical schema with `BIGINT`, `SERIAL`, and other PostgreSQL types.
3. **Batch cloning** — Copies data in 1000-row batches with parameterized queries.
4. **Sequence correction** — Resets PostgreSQL `SERIAL` sequences so new IDs don't collide.
5. **Search initialization** — Creates PostgreSQL full-text search triggers and GIN indexes.

For implementation details, see `plexichat/src/core/migrations/cli.py:376` and `plexichat/src/core/migrations/cloner.py`.

## Troubleshooting

### Checksum mismatch

You see a checksum mismatch warning. This means migration files on disk differ from those used to build the SQLite database. Verify that your codebase version matches the version that created the SQLite database. Run `python -m src.core.migrations.cli validate_migrations` to check integrity.

### Permission denied (triggers)

The utility fails during module initialization. Ensure your PostgreSQL user has `TRIGGER` privilege or owns the tables.

### Missing SQLite path

If your SQLite file is not at `data/plexichat.db`, pass `--sqlite-path`:

```bash
python -m src.core.migrations.cli migrate_to_postgres --sqlite-path ~/.plexichat/data/plexichat.db
```

## Related documentation

- [Database configuration](configuration/config-database.md)
- [Migration reference](../migration-reference.md)
- [Performance tuning](../performance.md)
