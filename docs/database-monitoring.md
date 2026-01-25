# Database Connection Pool Monitoring

## Overview

PlexiChat provides comprehensive monitoring and diagnostics for database connection pools. This documentation covers pool statistics collection, performance monitoring, health status determination, and integration with the admin API.

The monitoring system tracks connection lifecycle, detects performance issues, and provides real-time health metrics accessible through both programmatic APIs and the admin dashboard.

## Pool Statistics Metrics

The `get_pool_stats()` method returns a dictionary containing the following metrics:

### Connection Counts

- **active_connections**: Number of connections currently in use by application threads
- **idle_connections**: Number of connections available in the pool but not in use
- **total_connections**: Sum of active and idle connections
- **max_connections**: Maximum pool size configured
- **min_connections**: Minimum pool size configured

Example values:
- active_connections: 5
- idle_connections: 15
- total_connections: 20
- max_connections: 20
- min_connections: 2

### Performance Metrics

- **avg_acquisition_time**: Average time in seconds to acquire a connection from the pool
- **max_acquisition_time**: Maximum acquisition time observed
- **total_acquisitions**: Total number of connection acquisitions since startup
- **avg_pool_wait_time**: Average time waiting when pool is saturated (in seconds)
- **total_pool_waits**: Number of times a request had to wait for an available connection

Example values:
- avg_acquisition_time: 0.012
- max_acquisition_time: 0.156
- total_acquisitions: 1250
- avg_pool_wait_time: 0.0
- total_pool_waits: 0

### Connection Age Tracking

- **old_connections**: List of connections exceeding the maximum age threshold
  - Each entry contains:
    - connection_id: Python object ID of the connection
    - age_seconds: Current age of the connection in seconds
    - thread_id: Thread identifier where connection was created

Example:
```
old_connections: [
  {
    "connection_id": 12345678,
    "age_seconds": 1850.5,
    "thread_id": 139876543210
  }
]
```

### Metadata

- **database_type**: Type of database (postgres or sqlite)
- **timestamp**: ISO 8601 formatted timestamp when metrics were collected

Example:
- database_type: postgres
- timestamp: 2026-01-11T14:30:45.123456

## Enabling and Configuring Pool Monitoring

### Configuration

Pool monitoring is configured in `config.yaml` under the `connection_pool` and `monitoring` sections:

```yaml
connection_pool:
  min_connections: 1
  max_connections: 10
  connect_timeout: 10
  max_idle_time: 300
  validation_interval: 60
  enable_validation: true
  validation_query: "SELECT 1"

monitoring:
  enabled: true
  log_interval: 300
  metrics_enabled: true
```

Configuration parameters:

- **min_connections**: Minimum connections to maintain (default: 1)
- **max_connections**: Maximum allowed connections (default: 10)
- **connect_timeout**: Timeout in seconds for acquiring connections (default: 10)
- **max_idle_time**: Maximum idle time before evicting a connection in seconds (default: 300)
- **validation_interval**: How often to validate connections in seconds (default: 60)
- **enable_validation**: Enable connection validation queries (default: true)
- **validation_query**: Query to validate connection health (default: "SELECT 1")
- **max_connection_age_hours**: Maximum age before warning about long-lived connections (default: 0.5 hours = 30 minutes)

### Environment Variable Overrides

Configuration can be overridden using environment variables:

```
DB_POOL_MIN_CONNECTIONS=2
DB_POOL_MAX_CONNECTIONS=20
DB_POOL_CONNECT_TIMEOUT=15
DB_POOL_MAX_IDLE_TIME=600
DB_POOL_VALIDATION_INTERVAL=90
DB_POOL_ENABLE_VALIDATION=true
DB_POOL_VALIDATION_QUERY="SELECT 1"
MONITORING_ENABLED=true
MONITORING_LOG_INTERVAL=300
MONITORING_METRICS_ENABLED=true
```

### Starting Pool Monitoring

Pool monitoring is started programmatically using the `start_pool_monitoring()` method:

```python
from src.core.database.core import Database

db = Database()
db.start_pool_monitoring()
```

The monitoring thread:
- Runs as a daemon thread named "DatabasePoolMonitor"
- Logs pool statistics at the configured interval (default: 60 seconds)
- Checks for and warns about long-lived connections
- Is safe to call multiple times - only one monitoring thread will be started
- Can be stopped with `db.stop_pool_monitoring()`

Logging output example:
```
Pool stats - Active: 3, Idle: 7, Total: 10, Avg acq time: 0.015s
Connection 140234567890 has exceeded max age threshold (1850.5s > 1800.0s). Created in thread 140234567890.
```

### Stopping Pool Monitoring

Stop monitoring gracefully:

```python
db.stop_pool_monitoring()
```

This method:
- Signals the monitoring thread to stop
- Waits up to 5 seconds for the thread to finish
- Is safe to call even if monitoring is not running
- Can be called multiple times without error

## Admin API Pool Health Endpoint

### Endpoint Details

- **URL**: `/api/v1/admin/database/pool-health`
- **Method**: GET
- **Authentication**: Bearer token required
- **Rate Limiting**: Subject to admin API rate limits

### Request

```bash
curl -X GET http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

### Response Format

Successful response (HTTP 200):

```json
{
  "status": "healthy",
  "active_connections": 5,
  "idle_connections": 15,
  "total_connections": 20,
  "max_connections": 20,
  "min_connections": 2,
  "avg_acquisition_time": 0.012,
  "max_acquisition_time": 0.156,
  "avg_pool_wait_time": 0.0,
  "total_acquisitions": 1250,
  "total_pool_waits": 0,
  "old_connections": [],
  "database_type": "postgres",
  "timestamp": "2026-01-11T14:30:45.123456",
  "health_issues": []
}
```

### Status Values

The endpoint returns one of three status values:

- **healthy**: Pool is operating normally with no issues
- **warning**: Pool has some issues but is still functional
- **critical**: Pool is in a critical state and requires attention

### Error Responses

Authentication failure (HTTP 401):
```json
{
  "error": {
    "code": 401,
    "message": "Invalid or expired token"
  }
}
```

Host restriction blocked (HTTP 403):
```json
{
  "error": {
    "code": 403,
    "message": "Access denied (host restriction)"
  }
}
```

Admin UI disabled (HTTP 404):
```json
{
  "error": {
    "code": 404,
    "message": "Admin UI disabled"
  }
}
```

Server error (HTTP 500):
```json
{
  "error": {
    "code": 500,
    "message": "Database not initialized"
  }
}
```

## Alert Thresholds and Health Status Determination

### Default Alert Thresholds

Alert thresholds are configured in `config.yaml` under `monitoring.alert_thresholds`:

```yaml
alert_thresholds:
  cpu_percent: 80
  memory_percent: 85
  db_pool_saturation_percent: 75
  query_time_ms: 5000
  db_errors_per_minute: 10
  api_response_time_ms: 1000
```

Pool-specific thresholds:

- **db_pool_saturation_percent**: Alert when active connections exceed this percentage of max connections (default: 75%)
- **query_time_ms**: Alert when query execution exceeds this time in milliseconds (default: 5000)
- **db_errors_per_minute**: Alert when error rate exceeds this count (default: 10)

### Health Status Determination Logic

The admin endpoint applies the following logic to determine pool health:

1. **Pool Utilization Check**:
   - Utilization = active_connections / max_connections
   - Utilization > 90%: Set status to "warning" and add critical utilization issue
   - Utilization > 75%: Add high utilization warning (status remains healthy)
   - Utilization <= 75%: Pool utilization is acceptable

2. **Old Connections Check**:
   - If old_connections list is not empty: Set status to "warning"
   - Add message: "{count} long-lived connections detected"
   - Long-lived connections indicate stale pools that may need recycling

3. **Pool Exhaustion Check**:
   - If total_pool_waits > 0: Set status to "warning"
   - Add message: "Pool exhaustion detected: {count} wait events"
   - Indicates requests had to wait for available connections

4. **Acquisition Time Check**:
   - If avg_acquisition_time > 1.0 second: Set status to "warning"
   - Add message: "High acquisition time: {time:.3f}s average"
   - May indicate database overload or network issues

Final status determination:
- "healthy" if no issues are detected
- "warning" if any issues are detected but pool is functional
- "critical" if multiple critical issues occur simultaneously

### Custom Threshold Configuration

Override thresholds with environment variables:

```
MONITORING_ALERT_CPU_THRESHOLD=75
MONITORING_ALERT_MEMORY_THRESHOLD=80
MONITORING_ALERT_DB_POOL_THRESHOLD=70
MONITORING_ALERT_QUERY_TIME_MS=3000
MONITORING_ALERT_DB_ERRORS_PER_MINUTE=5
MONITORING_ALERT_API_RESPONSE_TIME_MS=500
```

The system will validate these values on startup and log warnings if invalid values are detected.

## Monitoring Queries and Examples

### Python API Usage

Get current pool statistics:

```python
from src.core.database.core import Database

db = Database()
stats = db.get_pool_stats()

print(f"Active connections: {stats['active_connections']}")
print(f"Pool utilization: {stats['active_connections'] / stats['max_connections'] * 100:.1f}%")
print(f"Avg acquisition time: {stats['avg_acquisition_time']:.3f}s")

if stats['old_connections']:
    for conn in stats['old_connections']:
        print(f"Old connection ID {conn['connection_id']} age: {conn['age_seconds']:.1f}s")
```

Monitor pool in background:

```python
db = Database()
db.start_pool_monitoring()

# Monitoring thread now logs stats every log_interval seconds
# ... application continues ...

# Later, stop monitoring
db.stop_pool_monitoring()
```

Check pool health programmatically:

```python
def check_pool_health(db):
    stats = db.get_pool_stats()
    
    utilization = stats['active_connections'] / stats['max_connections']
    
    if utilization > 0.9:
        return "CRITICAL", f"Pool at {utilization*100:.1f}% capacity"
    elif utilization > 0.75:
        return "WARNING", f"Pool at {utilization*100:.1f}% capacity"
    elif stats['old_connections']:
        return "WARNING", f"Found {len(stats['old_connections'])} long-lived connections"
    elif stats['total_pool_waits'] > 0:
        return "WARNING", f"Pool exhaustion: {stats['total_pool_waits']} wait events"
    else:
        return "HEALTHY", "All metrics within normal ranges"

status, message = check_pool_health(db)
print(f"{status}: {message}")
```

### REST API Usage

Using curl to fetch pool health:

```bash
# Get pool health
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq .

# Extract just the status
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq .status

# Check pool utilization
curl -s http://localhost:5000/api/v1/admin/database/pool-health \
  -H "Authorization: Bearer $ADMIN_TOKEN" | \
  jq '(.active_connections / .max_connections * 100) | floor'
```

Using Python requests:

```python
import requests
import json

admin_token = "your_admin_token"
headers = {"Authorization": f"Bearer {admin_token}"}

response = requests.get(
    "http://localhost:5000/api/v1/admin/database/pool-health",
    headers=headers
)

if response.status_code == 200:
    stats = response.json()
    print(json.dumps(stats, indent=2))
    
    # Check for issues
    if stats['health_issues']:
        print("\nHealth Issues Detected:")
        for issue in stats['health_issues']:
            print(f"  - {issue}")
else:
    print(f"Error: {response.status_code}")
    print(response.json())
```

### Dashboard Integration

The admin dashboard automatically integrates pool monitoring with visual displays:

1. **Real-time monitoring**: Dashboard fetches pool health every 5 seconds when monitoring is enabled
2. **Status indicator**: Color-coded status display (green for healthy, yellow for warning)
3. **Connection visualization**: Shows active vs idle connections with capacity gauge
4. **Metrics charts**: Displays acquisition time and pool utilization trends
5. **Alerts panel**: Lists all detected health issues with explanations
6. **Old connections table**: Shows details of long-lived connections

Enable monitoring in the admin dashboard:

```javascript
// Click "Start Monitoring" button
// Dashboard will poll /api/v1/admin/database/pool-health every 5 seconds
// Displays real-time metrics and alerts
```

Dashboard features:

- Pool health card showing current status
- Connection utilization gauge
- Acquisition time metrics
- List of detected health issues
- Old connections table with creation time and thread info
- Refresh interval: 5 seconds when monitoring is active

## Connection Age Tracking and Warning System

### How It Works

The monitoring system tracks the age of connections and warns when they exceed configured thresholds:

1. **Creation Tracking**: When a connection is acquired from the pool, metadata is recorded including creation timestamp and thread ID
2. **Age Calculation**: Age is calculated as: current_time - creation_timestamp
3. **Threshold Checking**: During monitoring, connections older than `max_connection_age_hours` are identified
4. **Warning Logging**: Old connections trigger warning logs with ID, age, and creation thread

### Configuration

Set maximum connection age in `config.yaml`:

```yaml
connection_pool:
  max_connection_age_hours: 0.5  # 30 minutes
```

Override with environment variable:

```
DB_POOL_MAX_CONNECTION_AGE_HOURS=1.0
```

### Age Thresholds

Recommended settings by use case:

- **Development**: 24 hours (very permissive for testing)
- **Production (standard)**: 0.5 hours (30 minutes, balances stability and resource usage)
- **Production (strict)**: 0.25 hours (15 minutes, aggressively recycles old connections)
- **Disabled**: 0 hours (no age limit, not recommended)

### Warning System

When a connection exceeds the age threshold, the monitoring system:

1. Identifies the old connection in `get_pool_stats()` results
2. Includes it in the `old_connections` list returned by the API
3. Logs a warning message during periodic monitoring:

```
WARNING: Long-lived connection detected - ID: 140234567890, Age: 1850.5s, Thread: 140234567890
```

4. Reports the issue through the health endpoint with status "warning"

### Handling Old Connections

When old connections are detected:

1. **Admin Dashboard**: Displays warning and list of old connections
2. **Programmatic Check**: Application can see old_connections in get_pool_stats()
3. **Manual Action**: Administrators can restart the application to force connection recycling
4. **Automatic Eviction**: On next use, connections exceeding idle timeout are evicted

Example response with old connections:

```json
{
  "status": "warning",
  "old_connections": [
    {
      "connection_id": 140234567890,
      "age_seconds": 1850.5,
      "thread_id": 140234567890
    },
    {
      "connection_id": 140234567891,
      "age_seconds": 2100.3,
      "thread_id": 140234567891
    }
  ],
  "health_issues": [
    "2 long-lived connections detected"
  ]
}
```

### Connection Eviction

Connections are evicted (closed and removed) when:

1. **Idle Timeout Exceeded**: Connection inactive for longer than `max_idle_time` seconds
2. **Validation Query Fails**: Connection fails health check query
3. **Connection Closed**: PostgreSQL pool detects closed connections
4. **Manual Restart**: Entire pool is reset on application restart

After eviction, a new connection is acquired on next database access.

### Monitoring Connection Age

Track connection age in monitoring:

```python
db = Database()

# Enable monitoring to log old connections
db.start_pool_monitoring()

# Check manually
stats = db.get_pool_stats()
if stats['old_connections']:
    print("Old connections found:")
    for conn in stats['old_connections']:
        print(f"  - ID {conn['connection_id']}: {conn['age_seconds']/3600:.1f} hours old")
        print(f"    Created in thread: {conn['thread_id']}")
```

## Best Practices

### Monitoring Configuration

1. **Development Environment**: Set log_interval to 60 seconds for active monitoring
2. **Production Environment**: Set log_interval to 300 seconds (5 minutes) to reduce log volume
3. **High-Traffic Systems**: Reduce max_idle_time to 180 seconds to recycle connections more aggressively
4. **Connection Age**: Keep max_connection_age_hours at 0.5 to prevent connection staleness

### Pool Configuration

1. **Max Connections**: Set to 20-50 for typical systems, higher for high-concurrency workloads
2. **Min Connections**: Set to 1-5 to maintain baseline availability
3. **Timeout**: Keep at 10-15 seconds, increase for high-latency networks
4. **Validation**: Enable validation for critical production systems

### Dashboard Monitoring

1. Check dashboard health status regularly (at least daily for production)
2. Review alert thresholds quarterly based on actual usage patterns
3. Archive or aggregate old metrics for trend analysis
4. Set up automated alerting when status transitions to "warning"

### Response to Alerts

1. **High Pool Utilization** (>75%):
   - Increase max_connections configuration
   - Profile application to reduce simultaneous connections
   - Check for connection leaks in application code

2. **Old Connections Detected**:
   - Review application restart/deployment schedule
   - Consider reducing max_connection_age_hours
   - Check for connection holder threads that are not releasing connections

3. **Pool Exhaustion** (total_pool_waits > 0):
   - Increase pool size immediately
   - Investigate what caused the wait event
   - Review application query efficiency

4. **High Acquisition Time** (>1.0s average):
   - Check database server load and responsiveness
   - Verify network connectivity and latency
   - Profile slow queries in application logs
   - Consider increasing pool size to reduce contention

## Troubleshooting

### "Pool monitoring thread already running"

This is expected if start_pool_monitoring() is called multiple times. The monitoring system safely ignores duplicate start requests.

### Old connections keep appearing

Indicates connections are not being released after use. Check:
- Application code is properly closing database connections
- Long-running transactions are not holding connections
- Background tasks are releasing connections after completion

### Pool exhaustion detected (high total_pool_waits)

Requests are waiting for available connections. Solutions:
- Increase max_connections in configuration
- Reduce transaction duration
- Review query performance
- Add connection pooling middleware if using external systems

### High acquisition time

Connections take too long to acquire from the pool. Causes:
- Database server is overloaded
- Network latency between app and database
- Validation query is slow
- Pool size is too small causing contention

### Admin endpoint returns "Database not initialized"

The database module is not available. Ensure:
- Database configuration is present in config.yaml
- Database module is properly imported
- Server startup completed successfully

## References

- Database Core Module: [src/core/database/core.py](../src/core/database/core.py)
- Admin Routes: [src/api/routes/admin.py](../src/api/routes/admin.py)
- Configuration: [config/config.yaml](../config/config.yaml)
- Tests: [src/tests/test_pool_monitoring.py](../src/tests/test_pool_monitoring.py)
