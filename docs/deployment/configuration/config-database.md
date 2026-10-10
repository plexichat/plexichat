# Database configuration

This guide describes how to configure the database for Plexichat.

## Configuration keys

All database settings are under the `database` key:

```yaml
database:
  # All database settings go here.
```

## Database type

Choose `sqlite` for development or small single-server deployments, and
`postgres` for production.

```yaml
database:
  type: sqlite  # or postgres
```

**SQLite**

Use SQLite when you have fewer than 100 concurrent users and you run a
single server process.

* Single-writer concurrency. Only one process can write at a time.
* File-based storage. Place the file on an SSD when possible.
* No built-in replication or high availability.
* Do not use SQLite with multiple workers.

SQLite uses WAL mode, enforces foreign keys, and has a busy timeout that
retries lock contention automatically.

**PostgreSQL**

Use PostgreSQL for production, for multi-worker deployments, and for
horizontal scaling or high availability.

* True multi-writer concurrency.
* Replication and high availability.
* Connection pooling.

PostgreSQL requires a separate server. See the
[Valkey configuration](config-valkey.md) for caching that reduces database
load.

## SQLite configuration

```yaml
database:
  type: sqlite
  path: /root/.plexichat/data/plexichat.db
```

The default path is `~/.plexichat/data/plexichat.db`. For production, use
an absolute path such as `/var/lib/plexichat/plexichat.db` and ensure the
application user can read and write the directory.

Automatically applied SQLite pragmas:

* `journal_mode=WAL`
* `synchronous=NORMAL`
* `busy_timeout` derived from connection handling (retried automatically)
* `foreign_keys=ON`

Monitor the database file size and WAL size. Run
`PRAGMA wal_checkpoint(TRUNCATE)` if the WAL grows large, and consider
periodic `VACUUM`. Use `pytest src/tests/test_sqlite_load.py` to validate
concurrency before production.

**Backups**

Copy the file during a maintenance window or use the SQLite online backup
API for zero-downtime backups.

## PostgreSQL configuration

```yaml
database:
  type: postgres
  postgres:
    host: ${POSTGRES_HOST:-localhost}
    port: 5432
    user: ${POSTGRES_USER:-postgres}
    password: ${POSTGRES_PASSWORD:-}
    dbname: ${POSTGRES_DBNAME:-plexichat}
    sslmode: ${POSTGRES_SSLMODE:-prefer}
```

You can also set these values with environment variables or a
`DATABASE_URL`:

* `DATABASE_URL=postgres://user:password@host:5432/plexichat?sslmode=require`
* `DATABASE_URL=sqlite:///var/lib/plexichat/plexichat.db`
* Individual overrides: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `POSTGRES_DBNAME`, `POSTGRES_SSLMODE`

If `DATABASE_URL` is set, it takes precedence over the individual
`postgres` keys. See `src/server/config_loader.py` for the exact
precedence.

**Connection parameters**

* Create a dedicated PostgreSQL user for Plexichat with limited
  permissions.
* Never commit passwords to version control. Use environment variables or
  your secrets manager.
* Create the database before first boot, or grant `CREATE DATABASE` to the
  user.

**SSL modes**

* `disable` - No encryption. Never use in production.
* `allow` - Try SSL, fall back to plain text.
* `prefer` - Try SSL, fall back if the server does not support it.
  Suitable for local development.
* `require` - Require SSL.
* `verify-ca` - Require SSL and verify the CA.
* `verify-full` - Require SSL, verify the CA, and verify the hostname.

For production, use `require` or higher. Use `verify-full` with public CA
certificates and `verify-ca` with self-signed certificates.

Automatically applied keepalive settings:

* `keepalives=1`, `keepalives_idle=60`, `keepalives_interval=10`,
  `keepalives_count=5`, `tcp_user_timeout=30000`

## Connection pool

```yaml
database:
  connection_pool:
    min_connections: 5
    max_connections: 100
    connect_timeout: 10
    cache_size_kb: -32000
    mmap_size: 268435456
    journal_size_limit: 67108864
    temp_store: MEMORY
    idle_reclaim_seconds: 5.0
```

* `min_connections` and `max_connections` apply to both PostgreSQL and
  SQLite.
* `connect_timeout` is the time to wait for a connection before failing
  (default 10 seconds).
* `cache_size_kb`, `mmap_size`, `journal_size_limit`, and `temp_store`
  are per-connection SQLite memory settings.
* `idle_reclaim_seconds` (default 5.0) reclaims leaked thread-local
  connections when the pool is exhausted instead of blocking for the full
  timeout. A connection owned by the calling thread is never reclaimed.
  The connection monitor also reaps connections idle longer than 60 seconds.

You can override pool sizes with environment variables: `DB_POOL_MIN_CONNECTIONS`,
`DB_POOL_MAX_CONNECTIONS`, `DB_POOL_CONNECT_TIMEOUT`, `DB_POOL_MAX_IDLE_TIME`,
`DB_POOL_VALIDATION_INTERVAL`, `DB_POOL_ENABLE_VALIDATION`, and
`DB_POOL_VALIDATION_QUERY` (only `SELECT 1`, `SELECT 1;`, or
`SELECT version();` are accepted).

Size your pool based on expected concurrent requests, average query
duration, and your PostgreSQL `max_connections`:

| Deployment | Min connections | Max connections | Notes |
|------------|----------------|-----------------|-------|
| Development | 1 | 5 | Single user |
| Small (<100 users) | 5 | 10 | Low concurrency |
| Medium (100-1000 users) | 5 | 30 | Moderate concurrency |
| Large (1000+ users) | 10 | 100 | High concurrency; consider read replicas |

Monitor active, idle, and waiting counts. Increase `max_connections` if
requests frequently wait, or decrease it if the database reports connection
limits. For thousands of connections, consider PgBouncer.

## Monitoring

```yaml
database:
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true
```

Queries slower than `slow_query_threshold_ms` are logged for investigation.
Review slow query logs regularly, add indexes for frequent slow queries, and
track execution time trends.

## Migrations

```yaml
database:
  migrations:
    auto_migrate: true
    migration_dir: /root/.plexichat/migrations
    irreversible_migration_delay_days: 7
```

* `auto_migrate` - Apply pending migrations on startup. Keep enabled for
  development. Disable for blue-green deployments and run migrations
  manually.
* `migration_dir` - Directory that contains migration files.
* `irreversible_migration_delay_days` - Delay before applying irreversible
  migrations (default 7 days).

Best practices for production:

1. Back up the database before migrating.
2. Test migrations in staging.
3. Run migrations during low-traffic periods.
4. Verify the schema and run smoke tests after.

## Related documentation

* [Default configuration reference](../../default-config.md)
* [Authentication configuration](config-authentication.md)
* [Valkey configuration](config-valkey.md)
* [Getting started](../getting-started.md)
