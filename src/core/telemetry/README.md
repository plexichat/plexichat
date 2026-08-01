# Telemetry Module

Collects and aggregates anonymized response time telemetry data from clients.

## Overview

This module enables opt-in client-side telemetry collection for monitoring API performance. Clients can submit response time measurements which are aggregated for admin dashboards.

## Features

- **Anonymized Collection**: Client identifiers are hashed, no PII is stored
- **Batch Submissions**: Clients can batch up to 100 entries per request
- **Rate Limiting**: Prevents abuse with configurable rate limits
- **Aggregation**: Calculates percentiles (p50, p95, p99), averages, and error rates
- **Time-Series Data**: Historical data bucketed by configurable intervals
- **Auto-Cleanup**: Old data can be purged automatically

## Usage

### Setup (in main.py)

```python
from src.core import telemetry
from src.core.database import Database

db = Database()
db.connect()
telemetry.setup(db)
```

### Submitting Telemetry (from API route)

```python
from src.core import telemetry

entries = [
    {
        "endpoint": "/api/v1/users/@me",
        "method": "GET",
        "response_time_ms": 45.2,
        "status_code": 200,
        "timestamp": 1704067200000
    }
]
accepted = telemetry.submit_response_times(entries, client_id="abc123")
```

### Getting Statistics (for admin dashboard)

```python
from src.core import telemetry

# Get stats for last 24 hours
stats = telemetry.get_endpoint_stats(hours=24)

for stat in stats:
    print(f"{stat.method} {stat.endpoint}: avg={stat.avg_response_time_ms:.2f}ms, p95={stat.p95_response_time_ms:.2f}ms")
```

### Getting Time-Series History

```python
history = telemetry.get_response_time_history(
    endpoint="/api/v1/messages",
    method="GET",
    hours=24,
    bucket_minutes=5
)
```

## Configuration

Add to `config.yaml`:

```yaml
telemetry:
  enabled: true
  rate_limit:
    max_per_minute: 10
  retention_days: 30
```

## Database Schema

```sql
CREATE TABLE telemetry_response_times (
    id INTEGER PRIMARY KEY,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    response_time_ms REAL NOT NULL,
    status_code INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    client_id TEXT
);
```

## API Endpoint

`POST /api/v1/telemetry/response-times`

See `src/api/routes/telemetry.py` for the API implementation.

## Privacy

- Client IDs are SHA-256 hashes of IP + User-Agent
- No personally identifiable information is stored
- Users must opt-in via client settings
- Data is automatically cleaned up after retention period
