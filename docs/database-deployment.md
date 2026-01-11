# Database Deployment Guide

## Overview

This guide provides step-by-step procedures for deploying database changes to PlexiChat, including configuration migration, rollback procedures, and troubleshooting guidance for common issues.

The deployment process is designed to be safe, reversible, and minimally disruptive to running systems.

## Pre-Deployment Checklist

Before deploying any database changes, complete the following checklist:

### Backup and Verification

- [ ] **Database Backup**: Create a full database backup (both data and schema)
  - For PostgreSQL: `pg_dump plexichat > backup_$(date +%Y%m%d_%H%M%S).sql`
  - For SQLite: `cp data/plexichat.db data/plexichat.db.backup_$(date +%Y%m%d_%H%M%S)`
- [ ] **Configuration Backup**: Save current config.yaml
  - `cp config/config.yaml config/config.yaml.backup_$(date +%Y%m%d_%H%M%S)`
- [ ] **Connection String Verified**: Test database connectivity with current credentials
- [ ] **Pool Configuration Recorded**: Document current pool settings (min/max connections, timeouts)

### System Health Checks

- [ ] **Current Monitoring Status**: Check pool health dashboard at `/admin`
  - Pool utilization below 75%
  - No active pool waits or exhaustion events
  - No long-lived connections (old_connections list empty)
- [ ] **Error Rate**: Verify current error rate is below 5%
  - Check `/admin` dashboard for InFailedSqlTransaction or PoolError occurrences
  - No connection timeout errors in past hour
- [ ] **Capacity Planning**: Verify sufficient system resources
  - Available disk space: at least 50% free
  - RAM available: at least 1GB free
  - CPU utilization: below 80% average over past hour

### Application Readiness

- [ ] **Version Mismatch Check**: Ensure all instances are on same application version
  - Verify via application version endpoint: `/api/v1/health`
  - All instances should return identical version string
- [ ] **Feature Flags**: Confirm any related feature flags are configured correctly
- [ ] **Migration Scripts**: Review and test database migration scripts
  - Verify migration logic is idempotent (safe to run multiple times)
  - Check for any manual intervention requirements

### Deployment Window

- [ ] **Off-Peak Time**: Schedule deployment during low-traffic window
  - Monitor application analytics for traffic patterns
  - Typical off-peak: nights 22:00-06:00, early mornings 03:00-06:00
- [ ] **Maintenance Window Notification**: Notify users of planned maintenance
- [ ] **Team Availability**: Ensure rollback personnel are available during and 1 hour after deployment

### Documentation

- [ ] **Rollback Plan**: Document exact rollback steps (see Rollback Plan section below)
- [ ] **Migration Steps**: Write out exact SQL commands and configuration changes
- [ ] **Verification Steps**: Define how to verify successful deployment

## Step-by-Step Deployment Procedure

### Phase 1: Pre-Deployment Preparation (Duration: 5-10 minutes)

#### Step 1.1: Take Final Backup

```bash
# PostgreSQL backup
pg_dump -h localhost -U postgres plexichat > \
  backups/pre_deployment_$(date +%Y%m%d_%H%M%S).sql

# SQLite backup
cp data/plexichat.db data/plexichat.db.pre_deployment_$(date +%Y%m%d_%H%M%S)
```

Store backups in a safe location, ideally on a separate server.

#### Step 1.2: Verify Current Configuration

```bash
# Check current database configuration
grep -A 10 "database:" config/config.yaml

# Verify database connectivity
python3 -c "
from src.core.database.core import Database
db = Database()
db.connect()
stats = db.get_pool_stats()
print(f'Database: {stats[\"database_type\"]}')
print(f'Connection: OK')
db.close()
"
```

#### Step 1.3: Document Baseline Metrics

```bash
# Record current pool metrics
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | tee deployment_baseline.json

# Check application health
curl -s http://localhost:5000/api/v1/health | tee health_baseline.json
```

### Phase 2: Apply Database Changes (Duration: 15-30 minutes)

#### Step 2.1: Stop Application (Optional, based on change type)

For **non-breaking schema changes** (add columns, new tables):
- Continue to Step 2.2 without stopping the application
- Changes are backward-compatible and don't affect running queries

For **breaking schema changes** (drop columns, rename tables, type changes):
- Stop the application to prevent query errors
- Use your process manager: `systemctl stop plexichat` or `docker-compose down`

#### Step 2.2: Apply Database Migrations

Migrations are idempotent and safe to run multiple times:

```python
from src.core.database.core import Database
from src.core.migrations import run_migrations

db = Database()
db.connect()
run_migrations(db)
db.close()
print("All migrations completed successfully")
```

Or automatically (on application startup):

```bash
# Application runs migrations automatically on boot
systemctl start plexichat
# or
docker-compose up -d
```

#### Step 2.3: Apply Configuration Changes

If migrating database type or changing pool settings:

```yaml
# config/config.yaml
database:
  # OLD: type: sqlite
  # NEW:
  type: postgres
  
  postgres:
    host: localhost
    port: 5432
    user: postgres
    password: your_secure_password
    dbname: plexichat
    sslmode: prefer
  
  connection_pool:
    min_connections: 2
    max_connections: 20
    connect_timeout: 10
    max_idle_time: 300
    validation_interval: 60
    enable_validation: true
    validation_query: "SELECT 1"
```

Alternatively, use environment variables (no config file changes):

```bash
export DATABASE_TYPE=postgres
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=your_secure_password
export POSTGRES_DBNAME=plexichat
export POSTGRES_SSLMODE=prefer
export DB_POOL_MIN_CONNECTIONS=2
export DB_POOL_MAX_CONNECTIONS=20
export DB_POOL_CONNECT_TIMEOUT=10
```

#### Step 2.4: Start Application (if stopped)

```bash
# Start application
systemctl start plexichat

# Verify application is running
sleep 5
curl http://localhost:5000/api/v1/health
```

### Phase 3: Post-Deployment Verification (Duration: 10-20 minutes)

#### Step 3.1: Verify Application Startup

```bash
# Check application logs for errors
journalctl -u plexichat -n 50 --no-pager

# Expected log messages:
# - "Database initialized with type: postgres"
# - "All migrations completed"
# - "Started pool monitoring thread"

# If errors appear, proceed to Rollback Plan section
```

#### Step 3.2: Verify Database Connectivity

```bash
# Test database connection and pool initialization
python3 -c "
from src.core.database.core import Database
db = Database()
db.connect()

# Get pool statistics
stats = db.get_pool_stats()
print(f'Database Type: {stats[\"database_type\"]}')
print(f'Active Connections: {stats[\"active_connections\"]}')
print(f'Idle Connections: {stats[\"idle_connections\"]}')
print(f'Timestamp: {stats[\"timestamp\"]}')

db.close()
print('Connection test: PASSED')
"

# Expected output:
# Database Type: postgres (or sqlite)
# Active Connections: 0-2
# Idle Connections: 1-5
# Connection test: PASSED
```

#### Step 3.3: Verify Migration Success

```bash
# Query database to verify schema changes
# Example: Check if new column exists

# PostgreSQL:
psql -U postgres -d plexichat -c "\d+ table_name"

# SQLite:
sqlite3 data/plexichat.db ".schema table_name"

# Verify all tables are accessible
python3 -c "
from src.core.database.core import Database
db = Database()
db.connect()

# Test a basic query
result = db.fetch_one('SELECT 1 as test')
assert result['test'] == 1

# Test on critical tables
tables_to_check = ['auth_users', 'msg_messages', 'srv_servers']
for table in tables_to_check:
    try:
        row = db.fetch_one(f'SELECT COUNT(*) as cnt FROM {table}')
        print(f'{table}: OK')
    except Exception as e:
        print(f'{table}: FAILED - {e}')

db.close()
"
```

#### Step 3.4: Verify Pool Health Metrics

```bash
# Compare with pre-deployment metrics
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | tee deployment_post.json

# Verify metrics are within expected ranges:
# - status: "healthy" (not "warning" or "critical")
# - active_connections: 0-5 (low traffic period)
# - avg_acquisition_time: < 0.05s (50ms)
# - total_pool_waits: 0 (no pool exhaustion)
# - old_connections: [] (empty list)
# - health_issues: [] (empty list)

python3 << 'EOF'
import json

with open('deployment_post.json') as f:
    data = json.load(f)

checks = [
    ("Status", data['status'] == 'healthy'),
    ("Pool Waits", data['total_pool_waits'] == 0),
    ("Old Connections", len(data['old_connections']) == 0),
    ("Health Issues", len(data.get('health_issues', [])) == 0),
]

all_pass = True
for check_name, result in checks:
    status = "PASS" if result else "FAIL"
    print(f"{check_name}: {status}")
    all_pass = all_pass and result

if all_pass:
    print("\nAll verification checks PASSED")
else:
    print("\nSome checks FAILED - review pool health")
EOF
```

#### Step 3.5: Run Smoke Tests

```bash
# Test critical API endpoints
test_user_id=1
test_server_id=1

# Health check
echo "Testing health endpoint..."
curl -s http://localhost:5000/api/v1/health | jq .

# User endpoint (requires auth token)
echo "Testing user endpoint..."
curl -s "http://localhost:5000/api/v1/users/$test_user_id" \
  -H "Authorization: Bearer $AUTH_TOKEN" | jq .

# Message endpoint
echo "Testing message endpoint..."
curl -s "http://localhost:5000/api/v1/channels/1/messages?limit=1" \
  -H "Authorization: Bearer $AUTH_TOKEN" | jq .

# All endpoints should return HTTP 200/201 (not 500)
```

#### Step 3.6: Monitor for Errors (Duration: 5-10 minutes)

Watch application logs for 5-10 minutes after deployment:

```bash
# Monitor logs in real-time
journalctl -u plexichat -f

# Look for these error patterns (should not appear):
# - "InFailedSqlTransaction"
# - "connection pool exhausted"
# - "connection timeout"
# - "constraint violation"
# - "data integrity"

# If errors appear during monitoring, initiate Rollback Plan
```

### Post-Deployment Monitoring Checklist

- [ ] **Error Rate**: Remains below 5% for 10 minutes
- [ ] **Response Times**: API endpoints respond in <500ms (p95)
- [ ] **Database Queries**: All queries execute successfully
- [ ] **User Reports**: No user-facing errors reported
- [ ] **Resource Usage**: CPU below 70%, Memory below 80%

## Rollback Plan

If deployment issues occur, follow this procedure to revert changes:

### Phase 1: Immediate Mitigation (Duration: 2-5 minutes)

#### Step 1.1: Assess Issue Severity

Determine if issue is severe enough to require rollback:

- **Severe** (Proceed to rollback):
  - Critical errors in application logs (cannot start)
  - Database connectivity failures
  - Data corruption detected
  - Error rate above 50%
  - Cascading failures affecting multiple features

- **Minor** (Troubleshoot without rollback):
  - Single feature affected
  - Isolated pool wait events
  - High but recovering error rate
  - Long-lived connections present but stable

#### Step 1.2: Stop Application

```bash
# Stop application immediately
systemctl stop plexichat
# or
docker-compose down

# Verify application stopped
sleep 2
curl -s http://localhost:5000/api/v1/health || echo "Confirmed: Application stopped"
```

#### Step 1.3: Preserve Current State (for debugging)

```bash
# Save current database state for analysis
pg_dump -h localhost -U postgres plexichat > \
  backups/failed_deployment_$(date +%Y%m%d_%H%M%S).sql

# Save current application logs
journalctl -u plexichat --no-pager > \
  logs/failed_deployment_$(date +%Y%m%d_%H%M%S).log
```

### Phase 2: Database Rollback (Duration: 5-30 minutes)

#### Step 2.1: Restore from Backup

**Option A: PostgreSQL Restore**

```bash
# Restore from SQL backup
psql -h localhost -U postgres -d plexichat < \
  backups/pre_deployment_YYYYMMDD_HHMMSS.sql

# Verify restore completed
psql -h localhost -U postgres -c "SELECT NOW();"
```

**Option B: SQLite Restore**

```bash
# Restore from file backup
cp data/plexichat.db.pre_deployment_YYYYMMDD_HHMMSS data/plexichat.db

# Verify restore
sqlite3 data/plexichat.db "SELECT COUNT(*) FROM auth_users;"
```

**Option C: Point-in-Time Recovery (PostgreSQL)**

If backup is not recent:

```bash
# Recover to time just before deployment
# (Requires WAL archiving to be enabled)
psql -h localhost -U postgres << EOF
SELECT pg_stop_backup();
SELECT pg_create_restore_point('pre_deployment');
-- Restore point created - see PostgreSQL documentation for recovery steps
EOF
```

#### Step 2.2: Verify Database Integrity

```bash
# Check table integrity
python3 << 'EOF'
from src.core.database.core import Database

db = Database()
db.connect()

# Check critical tables exist and have data
tables = {
    'auth_users': 'At least 1 user',
    'msg_messages': 'Core messages table',
    'srv_servers': 'Server data',
}

for table, description in tables.items():
    try:
        row = db.fetch_one(f'SELECT COUNT(*) as cnt FROM {table}')
        count = row['cnt']
        print(f'✓ {table}: {count} rows ({description})')
    except Exception as e:
        print(f'✗ {table}: {e}')

db.close()
EOF

# If any table is missing or corrupted, database restore failed
# Contact database administrator for manual recovery
```

### Phase 3: Configuration Rollback (Duration: 2-5 minutes)

#### Step 3.1: Restore Configuration

```bash
# Restore pre-deployment configuration
cp config/config.yaml.backup_YYYYMMDD_HHMMSS config/config.yaml

# Or restore environment variables
# Remove/reset any new environment variables added for deployment
unset DATABASE_TYPE
unset POSTGRES_HOST
unset POSTGRES_PORT
unset POSTGRES_USER
unset POSTGRES_PASSWORD
unset POSTGRES_DBNAME
```

#### Step 3.2: Verify Configuration

```bash
# Check configuration is correct
grep -A 10 "database:" config/config.yaml

# Verify connectivity with restored configuration
python3 -c "
from src.core.database.core import Database
db = Database()
db.connect()
print('Configuration: OK')
db.close()
"
```

### Phase 4: Application Restart (Duration: 2-5 minutes)

#### Step 4.1: Start Application

```bash
# Start application with restored configuration
systemctl start plexichat

# Wait for startup
sleep 5

# Verify startup
journalctl -u plexichat -n 20 --no-pager | grep -i "error" || echo "No errors in startup"
```

#### Step 4.2: Verify Application Health

```bash
# Check health endpoint
curl -s http://localhost:5000/api/v1/health | jq .

# Check database connectivity
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq .status

# Both should return 200 status
```

#### Step 4.3: Compare with Baseline Metrics

```bash
# Fetch current metrics
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" > deployment_rollback.json

# Compare with pre-deployment baseline
python3 << 'EOF'
import json

with open('deployment_baseline.json') as f:
    baseline = json.load(f)

with open('deployment_rollback.json') as f:
    current = json.load(f)

# Verify metrics match baseline (allowing for time-based variance)
checks = [
    ('Status', baseline['status'], current['status']),
    ('Database Type', baseline['database_type'], current['database_type']),
]

print("Metrics Comparison:")
for check_name, baseline_val, current_val in checks:
    match = baseline_val == current_val
    symbol = "✓" if match else "✗"
    print(f"{symbol} {check_name}: {baseline_val} -> {current_val}")
EOF
```

### Post-Rollback Actions

- [ ] **Notify Stakeholders**: Inform team of rollback and ETA for re-deployment
- [ ] **Root Cause Analysis**: Investigate why deployment failed
- [ ] **Update Plan**: Modify deployment procedure based on findings
- [ ] **Test Changes**: Test updated changes in staging environment
- [ ] **Schedule Re-Deployment**: Plan new deployment window after fixes

## Database Configuration Migration: SQLite to PostgreSQL

### Overview

Migrating from SQLite to PostgreSQL in production requires careful planning to minimize downtime and ensure data integrity.

**Estimated Migration Time**: 30 minutes to 2 hours (depending on data volume)

### Pre-Migration Checklist

- [ ] **PostgreSQL Server**: Installed, running, and accessible
- [ ] **Connection Parameters**: Database credentials verified
- [ ] **Storage**: Sufficient disk space on PostgreSQL server (1.5x current database size)
- [ ] **psycopg2-binary**: Installed in Python environment
- [ ] **Data Backup**: Full SQLite backup created

### Step 1: Prepare PostgreSQL Database

#### Step 1.1: Create Database and User

```sql
-- Connect to PostgreSQL as admin
psql -h localhost -U postgres

-- Create database
CREATE DATABASE plexichat;

-- Create user (change password to strong value)
CREATE USER plexi WITH PASSWORD 'your_secure_password';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE plexichat TO plexi;
ALTER DATABASE plexichat OWNER TO plexi;
```

#### Step 1.2: Verify Connectivity

```bash
# Test connection
psql -h localhost -U plexi -d plexichat -c "SELECT NOW();"

# Should output current timestamp
```

### Step 2: Configure Application for PostgreSQL

#### Step 2.1: Update Configuration

```yaml
# config/config.yaml
database:
  type: postgres  # Change from sqlite
  
  postgres:
    host: localhost
    port: 5432
    user: plexi
    password: your_secure_password
    dbname: plexichat
    sslmode: prefer
  
  # Optional: Adjust pool for PostgreSQL
  connection_pool:
    min_connections: 2      # Increased from 1
    max_connections: 20     # Increased from 10
    connect_timeout: 10
    max_idle_time: 300
    validation_interval: 60
    enable_validation: true
    validation_query: "SELECT 1"
```

Or use environment variables:

```bash
export DATABASE_TYPE=postgres
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_USER=plexi
export POSTGRES_PASSWORD=your_secure_password
export POSTGRES_DBNAME=plexichat
export POSTGRES_SSLMODE=prefer
export DB_POOL_MIN_CONNECTIONS=2
export DB_POOL_MAX_CONNECTIONS=20
```

### Step 3: Migrate Data from SQLite to PostgreSQL

#### Step 3.1: Export SQLite Data

```bash
# Generate SQL dump from SQLite
sqlite3 data/plexichat.db ".dump" > sqlite_dump.sql

# The dump will contain all CREATE TABLE and INSERT statements
```

#### Step 3.2: Adapt SQLite Dump for PostgreSQL

SQLite and PostgreSQL have some syntax differences. Create a migration script:

```python
# migrate_sqlite_to_postgres.py

import sqlite3
import psycopg2
import sys

def migrate_data(sqlite_path, postgres_conn_str):
    """Migrate data from SQLite to PostgreSQL."""
    
    # Connect to SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()
    
    # Connect to PostgreSQL
    pg_conn = psycopg2.connect(postgres_conn_str)
    pg_cursor = pg_conn.cursor()
    
    # Get list of tables from SQLite
    sqlite_cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "ORDER BY name"
    )
    tables = [row[0] for row in sqlite_cursor.fetchall()]
    
    print(f"Found {len(tables)} tables to migrate")
    
    for table_name in tables:
        print(f"Migrating table: {table_name}")
        
        # Get column info
        sqlite_cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in sqlite_cursor.fetchall()]
        
        # Get data from SQLite
        sqlite_cursor.execute(f"SELECT * FROM {table_name}")
        rows = sqlite_cursor.fetchall()
        
        if not rows:
            print(f"  - {table_name}: Empty (skipped)")
            continue
        
        # Insert data into PostgreSQL
        placeholders = ','.join(['%s'] * len(columns))
        column_list = ','.join(columns)
        insert_sql = f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders})"
        
        for row in rows:
            try:
                pg_cursor.execute(insert_sql, tuple(row))
            except psycopg2.Error as e:
                print(f"  - Error inserting row: {e}")
                # Continue with next row
        
        pg_conn.commit()
        print(f"  - {table_name}: {len(rows)} rows migrated")
    
    sqlite_cursor.close()
    sqlite_conn.close()
    
    pg_cursor.close()
    pg_conn.close()
    
    print("Migration complete")

if __name__ == '__main__':
    sqlite_path = 'data/plexichat.db'
    postgres_conn_str = 'postgresql://plexi:password@localhost/plexichat'
    
    migrate_data(sqlite_path, postgres_conn_str)
```

Run the migration:

```bash
python3 migrate_sqlite_to_postgres.py
```

#### Step 3.3: Verify Data Migration

```python
# verify_migration.py

import sqlite3
import psycopg2

def verify_migration(sqlite_path, postgres_conn_str):
    """Verify data was migrated correctly."""
    
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_cursor = sqlite_conn.cursor()
    
    pg_conn = psycopg2.connect(postgres_conn_str)
    pg_cursor = pg_conn.cursor()
    
    # Get tables from SQLite
    sqlite_cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in sqlite_cursor.fetchall()]
    
    print("Verifying migration...")
    all_match = True
    
    for table_name in tables:
        # Count rows in SQLite
        sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        sqlite_count = sqlite_cursor.fetchone()[0]
        
        # Count rows in PostgreSQL
        pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        pg_count = pg_cursor.fetchone()[0]
        
        match = sqlite_count == pg_count
        symbol = "✓" if match else "✗"
        print(f"{symbol} {table_name}: {sqlite_count} (SQLite) -> {pg_count} (PostgreSQL)")
        
        if not match:
            all_match = False
    
    if all_match:
        print("\nVerification PASSED: All data migrated correctly")
    else:
        print("\nVerification FAILED: Row counts do not match")
    
    sqlite_cursor.close()
    sqlite_conn.close()
    pg_cursor.close()
    pg_conn.close()
    
    return all_match

if __name__ == '__main__':
    sqlite_path = 'data/plexichat.db'
    postgres_conn_str = 'postgresql://plexi:password@localhost/plexichat'
    
    verify_migration(sqlite_path, postgres_conn_str)
```

Run verification:

```bash
python3 verify_migration.py
```

### Step 4: Switch Application to PostgreSQL

Follow the Step-by-Step Deployment Procedure above with configuration pointing to PostgreSQL.

### Step 5: Verify and Monitor

Perform all verification steps from Phase 3 of the deployment procedure.

Pay special attention to:

- [ ] **Connection Pool**: PostgreSQL uses different pool semantics
- [ ] **Query Performance**: Verify query times are acceptable
- [ ] **Error Logging**: Watch for PostgreSQL-specific errors (connection refused, SSL errors)
- [ ] **Constraint Enforcement**: PostgreSQL enforces constraints more strictly

## Connection Pool Sizing Recommendations

### Overview

Connection pool size dramatically affects performance. Wrong sizing causes either pool exhaustion or resource waste.

### Formula: Optimal Pool Size

```
Optimal Max Connections = (number of concurrent workers) + (number of background tasks) + 20% overhead

Optimal Min Connections = max(2, (number of concurrent workers / 10))
```

### Workload-Based Recommendations

#### Small Deployment (Single Server, <100 Concurrent Users)

```yaml
connection_pool:
  min_connections: 2
  max_connections: 10
  connect_timeout: 10
  max_idle_time: 300
  validation_interval: 60
  enable_validation: true
```

Characteristics:
- Low traffic: <10 requests/second
- Memory-constrained environments
- Development/staging environments

#### Medium Deployment (2-4 Servers, 100-1000 Concurrent Users)

```yaml
connection_pool:
  min_connections: 5
  max_connections: 20
  connect_timeout: 15
  max_idle_time: 300
  validation_interval: 60
  enable_validation: true
```

Characteristics:
- Medium traffic: 10-100 requests/second
- Modest server resources
- Business hours heavy traffic

#### Large Deployment (4+ Servers, >1000 Concurrent Users)

```yaml
connection_pool:
  min_connections: 10
  max_connections: 50
  connect_timeout: 20
  max_idle_time: 300
  validation_interval: 60
  enable_validation: true
```

Characteristics:
- High traffic: >100 requests/second
- Dedicated database server
- Round-the-clock usage

### Monitoring and Tuning

#### Metrics to Monitor

1. **Pool Utilization** (via `/admin/database/pool-health`):
   - **Good**: active_connections < max_connections * 0.75
   - **Warning**: active_connections > max_connections * 0.75
   - **Critical**: total_pool_waits > 0 (pool exhaustion occurred)

2. **Acquisition Time**:
   - **Good**: avg_acquisition_time < 0.05s (50ms)
   - **Warning**: avg_acquisition_time 0.05-0.5s
   - **Critical**: avg_acquisition_time > 0.5s or max_acquisition_time > 1.0s

3. **Connection Age**:
   - **Good**: old_connections is empty
   - **Warning**: old_connections list has entries
   - **Critical**: many old connections or connections older than 1 hour

#### Tuning Guide

**If pool_waits > 0 (Pool Exhaustion):**

1. Immediate: Increase max_connections by 50%
   ```yaml
   connection_pool:
     max_connections: 30  # Was 20
   ```

2. Medium-term: Optimize slow queries
   - Identify slow queries in application logs
   - Add database indexes
   - Refactor N+1 query patterns

3. Long-term: Scale horizontally
   - Add more application servers
   - Add read replicas for queries
   - Use query caching (Redis)

**If avg_acquisition_time > 0.5s (Slow Acquisition):**

1. Check database server load
   - CPU usage: `top` or cloud console
   - Disk I/O: `iostat -x 1`
   - Memory: `free -h`

2. Check network latency
   - Ping database: `ping database_host`
   - Check for packet loss

3. Increase pool size slightly
   ```yaml
   connection_pool:
     max_connections: 25  # Was 20
   ```

4. Enable connection validation
   ```yaml
   connection_pool:
     enable_validation: true
     validation_query: "SELECT 1"
   ```

**If old_connections detected (Stale Connections):**

1. Reduce max_connection_age_hours
   - Default: 0.5 hours (30 minutes)
   - Shorter: 0.25 hours (15 minutes)

2. Reduce max_idle_time
   - Default: 300 seconds
   - Shorter: 180 seconds (3 minutes)

3. Monitor connection closure
   - Check application logs for connection leaks
   - Verify all database cursors are closed

## Troubleshooting Guide

### Common Issues and Solutions

#### Issue 1: Pool Exhaustion (total_pool_waits > 0)

**Symptoms:**
- Error message: "connection pool exhausted"
- Admin dashboard shows status "warning" or "critical"
- total_pool_waits counter increasing

**Root Causes:**
1. **Too many concurrent requests**: Traffic exceeds pool capacity
2. **Long-running queries**: Connections held for extended periods
3. **Connection leaks**: Application not releasing connections
4. **Database overload**: Database server cannot keep up

**Solution:**

```bash
# Step 1: Check current pool stats
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq .

# Step 2: Analyze which connections are active
psql -U postgres -d plexichat -c "
  SELECT pid, usename, query, query_start
  FROM pg_stat_activity
  WHERE state != 'idle'
  ORDER BY query_start DESC;
"

# Step 3: Identify slow queries in application logs
grep "slow query\|duration.*ms" /var/log/plexichat/app.log | tail -20

# Step 4: Increase pool size
# Edit config.yaml:
# connection_pool:
#   max_connections: 30  # Increase from current value

# Step 5: Restart application
systemctl restart plexichat

# Step 6: Monitor for recovery
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.total_pool_waits'
# Should return 0 after fix
```

#### Issue 2: InFailedSqlTransaction Error

**Symptoms:**
- Error message: "current transaction is aborted"
- Subsequent queries in transaction fail
- Error occurs after constraint violation or syntax error

**Explanation:**
PostgreSQL enters "failed transaction" state after an error within a transaction. All subsequent queries fail until the transaction is rolled back or a savepoint is released.

**Root Causes:**
1. **Constraint violation**: INSERT/UPDATE violates unique, foreign key, or other constraint
2. **Syntax error**: Malformed SQL query in transaction
3. **Type mismatch**: Data type incompatibility
4. **Permission error**: Insufficient permissions within transaction

**Solution:**

The application handles recovery automatically:

```python
# Application code automatically detects and recovers from InFailedSqlTransaction
# No manual action needed in most cases

# If the error persists:
# 1. Check application logs for the original constraint violation
grep -B 5 "InFailedSqlTransaction\|current transaction is aborted" /var/log/plexichat/app.log

# 2. Fix the underlying issue (constraint violation, syntax error, etc.)

# 3. Verify the fix
python3 -c "
from src.core.database.core import Database
db = Database()
db.connect()

# Run the query that was failing
result = db.fetch_all('SELECT * FROM table_name LIMIT 1')
print(f'Query successful: {len(result)} rows')

db.close()
"
```

**Prevention:**
- Validate input before database operations
- Check constraints before INSERT/UPDATE
- Use prepared statements to prevent syntax errors
- Test transactions thoroughly before deployment

#### Issue 3: Connection Timeout

**Symptoms:**
- Error message: "connection timeout after 10s"
- Errors increase under load
- Some clients succeed, others fail

**Root Causes:**
1. **Network latency**: High latency between app and database
2. **Database overloaded**: Cannot accept new connections
3. **Connection limit exceeded**: All database connections in use
4. **Firewall/network issue**: Packets lost or blocked

**Solution:**

```bash
# Step 1: Check network connectivity
ping -c 4 database_host
# Acceptable: <50ms latency, 0% packet loss

# Step 2: Check database connection count
psql -U postgres -c "SELECT COUNT(*) FROM pg_stat_activity;"
# Should be less than max_connections - 5

# Step 3: Increase connection timeout
# Edit config.yaml:
# connection_pool:
#   connect_timeout: 20  # Was 10

# Step 4: Check if database accepts connections
psql -h database_host -U postgres -c "SELECT NOW();"
# Should return immediately

# Step 5: Monitor after fix
watch -n 1 'curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq ".avg_acquisition_time"'
```

#### Issue 4: High Acquisition Time (avg > 0.5s)

**Symptoms:**
- Admin dashboard shows avg_acquisition_time > 0.5s
- API response times slow
- User reports delays

**Root Causes:**
1. **Database server overloaded**: High CPU/memory usage
2. **Validation queries slow**: "SELECT 1" taking >100ms
3. **Network latency**: Slow connection to database
4. **Pool contention**: Many threads waiting for connections

**Solution:**

```bash
# Step 1: Check database server health
# On database server:
top -b -n 1 | head -20  # Check CPU usage
free -h                  # Check memory usage
iostat -x 1             # Check disk I/O

# Step 2: Check if validation is slow
psql -U postgres -c "EXPLAIN ANALYZE SELECT 1;"
# Should execute instantly (<1ms)

# Step 3: Disable validation if slow
# Edit config.yaml:
# connection_pool:
#   enable_validation: false  # Disable if SELECT 1 is slow

# Step 4: Check network latency
time psql -h database_host -U postgres -c "SELECT NOW();"
# Connection time should be <100ms

# Step 5: Monitor acquisition time after fix
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.avg_acquisition_time'
# Should be <0.05s (50ms) when healthy
```

#### Issue 5: Old Connections Detected

**Symptoms:**
- Admin dashboard shows old_connections is non-empty
- Connections age: hours or days
- Status: "warning"

**Root Causes:**
1. **Application not restarted**: Connections persist from old version
2. **Long-running transactions**: Connections held indefinitely
3. **Connection leak**: Application code not closing connections
4. **Idle connections**: Connections in pool but not being refreshed

**Solution:**

```bash
# Step 1: Check which connections are old
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.old_connections'

# Step 2: Identify source in application code
# Look for long-running transactions:
grep -r "begin_transaction" src/ | grep -v "test"

# Step 3: Reduce max_connection_age_hours
# Edit config.yaml:
# connection_pool:
#   max_connection_age_hours: 0.25  # Was 0.5 (15 min vs 30 min)

# Step 4: Restart application to force new connections
systemctl restart plexichat

# Step 5: Verify old connections are gone
sleep 30
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.old_connections'
# Should be empty: []
```

#### Issue 6: Data Integrity Issues

**Symptoms:**
- Data missing or corrupted after deployment
- Referential integrity violations
- Queries return unexpected results

**Root Causes:**
1. **Migration script error**: Schema change corrupted data
2. **Incomplete backup restore**: Restore didn't complete
3. **Database corruption**: Rare hardware issue
4. **Application bug**: Introduced in new code

**Solution:**

```bash
# Step 1: Verify backup integrity
# Size check - should be similar to original
ls -lh backups/pre_deployment*.sql

# Step 2: Check table integrity (PostgreSQL)
psql -U postgres -d plexichat -c "
  SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename))
  FROM pg_tables
  WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
  ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"

# Step 3: Check for constraint violations
psql -U postgres -d plexichat -c "
  SELECT * FROM auth_users WHERE id NOT IN (SELECT user_id FROM srv_members);
"
# Should return 0 rows (all users should be members somewhere)

# Step 4: If corruption confirmed, restore from backup
# Follow Rollback Plan section above
```

#### Issue 7: Migration Script Fails

**Symptoms:**
- Migration script errors on startup
- Specific columns or tables not created
- Error: "alter table failed" or "column already exists"

**Root Causes:**
1. **Schema already exists**: Running migrations twice
2. **Syntax error in migration**: Typo in ALTER TABLE
3. **Database type mismatch**: SQLite syntax used for PostgreSQL
4. **Permission error**: User lacks ALTER TABLE permission

**Solution:**

```bash
# Step 1: Check which migrations failed
journalctl -u plexichat | grep "Migration\|ALTER TABLE"

# Step 2: Verify schema state
# PostgreSQL:
psql -U postgres -d plexichat -c "
  SELECT column_name FROM information_schema.columns
  WHERE table_name = 'auth_users'
  ORDER BY ordinal_position;
"

# SQLite:
sqlite3 data/plexichat.db ".schema auth_users"

# Step 3: Manually apply missing migration if needed
# Edit migrations.py or run migration manually:
python3 -c "
from src.core.database.core import Database
from src.core.migrations import run_migrations

db = Database()
db.connect()
run_migrations(db)
db.close()
"

# Step 4: Verify migration completed
sqlite3 data/plexichat.db ".schema" | grep "column_name_here"
```

## Deployment Safety Checklist (Review Before Each Deployment)

- [ ] **Database Backup Created**: Full backup exists with recent timestamp
- [ ] **Configuration Backed Up**: config.yaml saved
- [ ] **Connection Tested**: Can connect to target database
- [ ] **Migration Scripts Reviewed**: No syntax errors or data loss operations
- [ ] **Rollback Plan Written**: Specific rollback steps documented
- [ ] **Off-Peak Window**: Deployment scheduled during low traffic
- [ ] **Team Notified**: Stakeholders informed of planned changes
- [ ] **Monitoring Enabled**: Admin dashboard accessible, logging enabled
- [ ] **Verification Steps Prepared**: Test cases ready to run
- [ ] **Incident Contact**: On-call engineer available for next 2 hours

## Post-Deployment Monitoring (First 24 Hours)

**Hour 1-2 (Critical Monitoring):**
- [ ] Monitor error rate continuously
- [ ] Check pool health every 5 minutes
- [ ] Watch for InFailedSqlTransaction errors
- [ ] Verify API response times

**Hour 2-4 (Active Monitoring):**
- [ ] Monitor every 15 minutes
- [ ] Check database performance metrics
- [ ] Verify no cascading failures
- [ ] User feedback monitoring

**Hour 4-24 (Regular Monitoring):**
- [ ] Check every 1-2 hours
- [ ] Monitor overnight if applicable
- [ ] Daily summary of error rates
- [ ] Performance trends analysis

## References

- [Database Monitoring Documentation](database-monitoring.md)
- [Configuration Guide](configuration.md)
- [Error Handling Guide](errors.md)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [SQLite Documentation](https://www.sqlite.org/docs.html)
