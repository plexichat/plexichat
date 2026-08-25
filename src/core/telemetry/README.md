# Telemetry Module

Collects and aggregates anonymized response time telemetry data from clients.

## Overview

This module enables **opt-in** client-side telemetry collection for monitoring API performance. Clients can submit response time measurements which are aggregated for admin dashboards. Telemetry is **disabled by default**: it only runs when `telemetry.enabled` is explicitly set to `true` in configuration.

## Features

- **Opt-In Only**: Disabled unless explicitly enabled in config; users must opt in via client settings
- **Anonymized Collection**: Client identifiers are salted SHA-256 digests (never raw user IDs); no PII is stored
- **Batch Submissions**: Clients can batch up to 100 entries per request
- **Rate Limiting**: Prevents abuse with configurable rate limits (`telemetry.rate_limit.max_per_minute`)
- **Cardinality Protection**: Client endpoints are allowlisted against registered route templates; unrecognized endpoints collapse into an `_other` overflow bucket, distinct endpoint keys are capped (`telemetry.max_distinct_endpoints`), and total stored rows are hard-capped (`telemetry.max_total_rows`) with a rejection counter exposed via `get_cardinality_stats()`
- **Aggregation**: Calculates percentiles (p50, p95, p99), averages, and error rates; percentile sampling is bounded in SQL (`telemetry.percentile_sample_rows`)
- **Time-Series Data**: Historical data bucketed by configurable intervals
- **Auto-Cleanup**: A background retention sweep deletes data older than `telemetry.retention_days` on every application start (lifespan task)

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
  enabled: false            # opt-in: set true to enable telemetry
  rate_limit:
    max_per_minute: 10
  retention_days: 30
  max_distinct_endpoints: 500
  max_total_rows: 100000
  percentile_sample_rows: 25000
  client_hash_salt: "<random per-install secret>"
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

- Client IDs are salted SHA-256 hashes of the **user ID** (salted per install via `telemetry.client_hash_salt`); raw user IDs are never stored
- No personally identifiable information is stored
- Telemetry is disabled unless explicitly enabled; users must opt in via client settings
- Data is automatically cleaned up by a background retention sweep after `retention_days`
- Entries submitted without a timestamp default to server-receive time
