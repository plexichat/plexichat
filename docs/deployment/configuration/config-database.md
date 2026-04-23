# Database Configuration

This guide covers database configuration for deploying Plexichat in production. Database settings directly impact performance, scalability, reliability, and operational complexity. Carefully review each section and choose the appropriate configuration for your deployment scale and requirements.

## Configuration Location

All database settings are nested under the `database` key in your configuration file:

```yaml
database:
  # All database settings go here
```

## Database Type Selection

Choose between PostgreSQL (recommended for production) or SQLite (development/small deployments).

### Configuration

```yaml
database:
  type: "sqlite"  # or "postgres"
```

### Deployment Considerations

**Why This Matters**

The database engine choice determines your deployment's scalability, performance characteristics, and operational requirements. This is one of the most critical infrastructure decisions you will make.

**SQLite**

**When to Use**

- Development and testing environments
- Small deployments with fewer than 100 concurrent users
- Single-server deployments without horizontal scaling requirements
- Scenarios where operational simplicity is prioritized over scalability

**Limitations**

- Single-writer concurrency (only one process can write at a time)
- File-based storage requires filesystem I/O performance
- No built-in replication or high availability
- Not suitable for multi-worker deployments (multiple server processes)

**Operational Notes**

- SQLite uses Write-Ahead Logging (WAL) mode for improved concurrency
- Foreign key constraints are enforced by default
- Busy timeout of 30 seconds allows handling temporary locks
- Database file is created automatically if it doesn't exist

**PostgreSQL**

**When to Use**

- Production deployments of any scale
- Multi-worker deployments (multiple server processes sharing the same database)
- For high-availability requirements (with PostgreSQL replication). See [Deployment Guide](deployment.md#horizontal-scaling) for horizontal scaling strategies.
- Large user bases (hundreds to thousands of concurrent users)
- Deployments requiring advanced database features

**Advantages**

- True multi-writer concurrency
- Mature replication and high availability options
- Advanced indexing and query optimization
- Connection pooling for efficient resource utilization
- Better performance for complex queries

**Operational Notes**

- Requires separate PostgreSQL server installation and maintenance. PostgreSQL connection pooling is essential for performance. See [Redis Configuration](deployment/configuration/config-redis.md) for caching configuration that can reduce database load.
- Requires monitoring of connection counts and query performance
- Backup and restore procedures differ from SQLite

---

## SQLite Configuration

Settings for SQLite database (used when `database.type` is "sqlite").

### Configuration

```yaml
database:
  type: "sqlite"
  path: "data/plexichat.db"
```

### Deployment Considerations

**File Path**

- **Default**: `data/plexichat.db` relative to the application directory
- **Production Recommendation**: Use an absolute path to a dedicated data directory (e.g., `/var/lib/plexichat/plexichat.db`)
- **Permissions**: Ensure the application user has read/write permissions to the directory and parent directories
- **Filesystem**: Place on a filesystem with good I/O performance (SSD preferred for production)

**SQLite PRAGMA Settings (Automatically Applied)**

The following SQLite settings are applied automatically and should not need adjustment:

- **journal_mode=WAL**: Write-Ahead Logging allows concurrent readers and writers
- **synchronous=NORMAL**: Balances data safety with performance (commits are durable but may be delayed slightly)
- **busy_timeout=30000**: Waits up to 30 seconds for database locks before failing
- **foreign_keys=ON**: Enforces referential integrity for foreign key constraints

**Performance Considerations**

- WAL mode significantly improves read concurrency but doubles disk space usage temporarily
- Consider running periodic `VACUUM` operations to reclaim space (not currently automated)
- Monitor filesystem I/O wait times during peak usage
- For high write throughput, consider placing the database on a dedicated disk

**Backup Strategy**

- SQLite backups are simple file copies, but must be taken while the database is not being written
- Use the SQLite backup API or copy the file during maintenance windows
- Consider using SQLite's online backup feature for zero-downtime backups

---

## PostgreSQL Configuration

Settings for PostgreSQL database (used when `database.type` is "postgres").

### Configuration

```yaml
database:
  type: "postgres"
  postgres:
    host: "localhost"
    port: 5432
    user: "postgres"
    password: ""
    dbname: "plexichat"
    sslmode: "prefer"
```

### Deployment Considerations

**Connection Parameters**

**Host and Port**

- **Localhost**: Appropriate for single-server deployments where PostgreSQL runs on the same machine
- **Remote Host**: Use for dedicated database servers or cloud database services (AWS RDS, Google Cloud SQL, etc.)
- **Port**: Default 5432 is standard. Change only if your PostgreSQL server uses a non-standard port

**User and Password**

- **Dedicated User**: Create a dedicated PostgreSQL user for Plexichat with limited permissions
- **Password Security**: Use strong, randomly generated passwords. Store securely (environment variables, secrets management)
- **Never Hardcode**: Never commit passwords to version control. Use environment variables or configuration management tools

**Database Name**

- Create the database manually before first deployment, or allow Plexichat to create it if the user has CREATE DATABASE permissions
- Use a descriptive name to identify the application database

**SSL Mode**

- **disable**: No encryption (never use in production)
- **allow**: Try SSL, accept unencrypted connection if SSL fails (not recommended for production)
- **prefer**: Try SSL, accept unencrypted if server doesn't support SSL (appropriate for local development)
- **require**: Require SSL, fail if SSL not available (recommended for production)
- **verify-ca**: Require SSL and verify CA certificate (recommended for production with self-signed certs)
- **verify-full**: Require SSL, verify CA certificate, and verify hostname (recommended for production with public CA certs)

**Production SSL Recommendations**

- Use `require` or higher for all production deployments
- Use `verify-full` when using certificates from a trusted CA (Let's Encrypt, commercial CAs)
- Use `verify-ca` when using self-signed certificates with a known CA
- Ensure your PostgreSQL server is configured to accept SSL connections

**PostgreSQL Keepalive Settings (Automatically Applied)**

The following TCP keepalive settings are applied automatically to detect dead connections:

- **keepalives=1**: Enable TCP keepalives
- **keepalives_idle=60**: Send first keepalive after 60 seconds of inactivity
- **keepalives_interval=10**: Send keepalive probes every 10 seconds
- **keepalives_count=5**: Declare connection dead after 5 missed keepalives (50 seconds total)
- **tcp_user_timeout=30000**: TCP user timeout of 30 seconds

These settings help detect network failures and stale connections without waiting for TCP timeouts.

---

## Connection Pool

Connection pool settings for PostgreSQL (ignored for SQLite).

### Configuration

```yaml
database:
  connection_pool:
    min_connections: 5
    max_connections: 100
    connect_timeout: 10
```

### Deployment Considerations

**Why Connection Pooling Matters**

Creating new database connections is expensive (requires network round-trip, authentication, memory allocation). Connection pooling reuses existing connections, significantly improving performance.

**Minimum Connections**

- **Default**: 5 connections
- **Small Deployment**: 2-5 connections sufficient for low-traffic deployments
- **Medium Deployment**: 5-10 connections for moderate traffic
- **Large Deployment**: 10-20 connections to handle baseline load

**Rationale**: Minimum connections are always kept open, ready for immediate use. Too few causes connection setup delays during traffic spikes. Too many wastes resources.

**Maximum Connections**

- **Default**: 100 connections
- **Small Deployment**: 10-20 connections
- **Medium Deployment**: 20-50 connections
- **Large Deployment**: 50-100 connections (ensure PostgreSQL max_connections is configured higher)

**Rationale**: Maximum connections limit resource usage and prevent overwhelming the database server. Calculate based on:
- Expected concurrent requests
- Average query duration
- PostgreSQL server capacity
- Other applications sharing the database server

**Formula**: `max_connections = (expected_concurrent_requests / average_queries_per_connection) + safety_margin`

**Connection Timeout**

- **Default**: 10 seconds
- **Production**: 5-10 seconds is appropriate
- **High-Latency Networks**: Increase to 15-20 seconds for geographically distributed deployments

**Rationale**: Timeout prevents indefinite waiting when database is unavailable or overloaded. Too short causes unnecessary failures during brief load spikes. Too long delays error reporting.

**Pool Sizing Guidelines**

- Deployment Scale: Development | Min Connections: 1 | Max Connections: 5 | Notes: Single user, minimal load
- Deployment Scale: Small (<100 users) | Min Connections: 5 | Max Connections: 10 | Notes: Low concurrency
- Deployment Scale: Medium (100-1000 users) | Min Connections: 5 | Max Connections: 30 | Notes: Moderate concurrency
- Deployment Scale: Large (1000+ users) | Min Connections: 10 | Max Connections: 100 | Notes: High concurrency, consider read replicas

**Operational Notes**

- Monitor pool utilization metrics (active, idle, waiting connections)
- Increase max_connections if you see frequent connection wait times
- Decrease max_connections if you see database connection limit errors
- Consider PgBouncer for very high-connection scenarios (thousands of connections)

---

## Monitoring

Database performance monitoring thresholds.

### Configuration

```yaml
database:
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true
```

### Deployment Considerations

**Why Monitoring Matters**

Database performance directly impacts user experience. Slow queries indicate performance issues that need investigation before they become critical.

**Slow Query Threshold**

- **Default**: 1000 milliseconds (1 second)
- **Standard Deployment**: 1000ms is appropriate for most applications
- **High-Performance Deployment**: Reduce to 500ms to catch performance regressions earlier
- **Complex Queries**: Increase to 2000-5000ms if you have inherently complex operations

**Rationale**: Threshold determines what constitutes a "slow" query requiring investigation. Too low generates noise. Too high misses real issues.

**Alert on Slow Queries**

- **Enable** for production to log slow queries for analysis
- **Disable** temporarily if generating excessive log volume during troubleshooting
- Logs include query text, execution time, and context for optimization

**Operational Notes**

- Regularly review slow query logs to identify optimization opportunities
- Add database indexes for frequently slow queries
- Consider query rewriting or schema changes for persistent performance issues
- Monitor query execution time trends over time

---

## Migration Settings

Database migration configuration.

### Configuration

```yaml
database:
  migrations:
    auto_migrate: true
    migration_dir: "migrations"
```

### Deployment Considerations

**Why Migrations Matter**

Database schema evolves over time. Migrations ensure your database schema stays synchronized with the application code.

**Auto-Migrate**

- **Default**: Enabled
- **Development**: Keep enabled for convenience
- **Production**: Consider disabling for manual control over schema changes
- **Blue-Green Deployments**: Disable and run migrations manually during deployment

**Rationale**: Auto-migrate ensures schema is always up-to-date but can cause unexpected downtime if migrations fail. Manual control allows planning and rollback.

**Migration Directory**

- **Default**: `migrations` relative to application directory
- **Custom**: Specify absolute path if using a centralized migration repository
- **Version Control**: Keep migration files in version control alongside application code

**Operational Notes**

- Always test migrations on a staging database before production deployment
- Review migration SQL files before deployment to understand schema changes
- Have a rollback plan for each migration (reverse migration SQL)
- For critical deployments, create database backups before running migrations
- Consider using transactional migrations (all-or-nothing) when possible

**Production Migration Best Practices**

1. **Backup First**: Always create a database backup before running migrations
2. **Test in Staging**: Verify migrations work on a staging environment first
3. **Schedule Maintenance**: Run migrations during low-traffic periods
4. **Monitor Logs**: Watch migration logs for errors or warnings
5. **Verify Schema**: Confirm schema changes are applied correctly
6. **Application Testing**: Run smoke tests to ensure application works with new schema

---

## Scaling Considerations

### Vertical Scaling (Single Server)

**When to Use**

- Deployments with fewer than 10,000 users
- Simpler operational requirements
- Limited budget for multiple servers

**Configuration Tips**

- Use PostgreSQL for better performance under load
- Increase connection pool size based on CPU cores and memory
- Enable query monitoring to identify bottlenecks
- Consider read replicas for read-heavy workloads

### Horizontal Scaling (Multiple Servers)

**When to Use**

- Deployments with more than 10,000 users
- High availability requirements
- Geographic distribution

**Configuration Tips**

- Use PostgreSQL (SQLite cannot scale horizontally)
- Place database on dedicated server or managed service
- Use connection pooling to limit database connections
- Consider database proxy (PgBouncer) for connection management
- Implement read replicas for read-heavy queries
- Monitor database replication lag

### Database as a Service

**Options**

- AWS RDS PostgreSQL
- Google Cloud SQL
- Azure Database for PostgreSQL
- DigitalOcean Managed Databases

**Configuration Tips**

- Use connection strings provided by the service
- Enable SSL/TLS for all connections
- Configure connection pool based on service tier limits
- Use service-provided backups and point-in-time recovery
- Monitor service metrics through provider dashboard

---

## Backup and Recovery

### SQLite Backups

**Strategy**

- Simple file copy during maintenance window
- Use SQLite online backup API for zero-downtime backups
- Store backups in multiple locations (local, remote)

**Procedure**

1. Stop application or ensure no write operations
2. Copy database file to backup location
3. Verify backup integrity
4. Store backup with timestamp

**Recovery**

1. Stop application
2. Replace database file with backup
3. Verify file permissions
4. Start application

### PostgreSQL Backups

**Strategy**

- Use `pg_dump` for logical backups
- Use physical backups (WAL archiving) for point-in-time recovery
- Leverage managed service backups if using database as a service

**Procedure (pg_dump)**

```bash
pg_dump -h localhost -U plexichat -d plexichat > backup.sql
```

**Procedure (WAL Archiving)**

Configure PostgreSQL to archive WAL files to a remote location for point-in-time recovery.

**Recovery**

```bash
psql -h localhost -U plexichat -d plexichat < backup.sql
```

---

## Related Documentation

- [Default Configuration Reference](../../default-config.md) - Complete configuration reference
- [Authentication Configuration](deployment/configuration/config-authentication.md) - Session storage and user data
- [Redis Configuration](deployment/configuration/config-redis.md) - Caching to reduce database load
- [Deployment Guide](../getting-started.md) - Production deployment and scaling
