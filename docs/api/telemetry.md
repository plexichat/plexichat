# Telemetry API

Ingest client-side operational signals — response-time measurements and Content Security Policy violation reports.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

Both endpoints are mounted under `/telemetry` (`plexichat/src/api/routes/telemetry.py:21`). The full paths are `/api/v1/telemetry/response-times` and `/api/v1/telemetry/csp-report` when the API prefix is `/api/v1` (`plexichat/src/config_defaults.py:132`).

Telemetry is optional and privacy-preserving. Don't include tokens, secrets, or message content.

## Overview

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/api/v1/telemetry/response-times` | Optional | Submit batches of client-observed response times |
| `POST` | `/api/v1/telemetry/csp-report` | None (browser callback) | Submit a CSP violation report |

Telemetry is disabled by default (`telemetry.enabled: false` in `plexichat/src/config_defaults.py:832`). When disabled or not yet initialized, submissions are accepted but dropped quietly with `accepted: 0` — the server logs at `DEBUG`, not `ERROR`.

## Submit response times

```
POST /api/v1/telemetry/response-times
```

Records client-perceived performance measurements for analysis. The endpoint is rate-limited and anonymizes authenticated users.

### Authentication

Optional. If you send `Authorization: Bearer <token>`, the server hashes your user ID with a per-install salt and stores `anon_<hex>` instead of the raw ID (`plexichat/src/api/routes/telemetry.py:32`):

```
client_id = "anon_" + SHA256(salt + ":" + user_id)[:32]
```

The salt comes from `telemetry.client_hash_salt`. If you don't authenticate, `client_id` is `null`.

### Rate limiting

The route calls `ratelimit.check_rate_limit` with `route="POST /telemetry"` and the client's IP (`plexichat/src/api/routes/telemetry.py:64`). Limits come from `telemetry.rate_limit.max_per_minute` (default 10).

If the limit is exceeded, the server returns `429 Telemetry submission rate limit exceeded`.

### Request body

`TelemetrySubmission` (`plexichat/src/api/schemas/telemetry.py:30`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entries` | array | Yes | 1–100 entries (`max_length=100`). Each entry is a `ResponseTimeEntry`. |

`ResponseTimeEntry` (`plexichat/src/api/schemas/telemetry.py:5`):

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `endpoint` | string | Yes | Max 255 characters | Route being measured. The server allowlists templates from live `APIRoute` objects so cardinality is bounded. |
| `method` | string | Yes | Max 10 characters | HTTP method. |
| `response_time_ms` | float | Yes | `>= 0` | Client-observed response time in milliseconds. |
| `status_code` | integer or string | Yes | — | HTTP status returned. |
| `db_queries` | integer | No | Default `0` | Number of DB queries for this request. |
| `db_time_ms` | float | No | Default `0.0` | Time spent in DB queries (ms). |
| `timestamp` | integer? | No | Unix seconds | When the measurement was taken. If omitted or `null`, the server defaults to receive time. |

Set `timestamp` only when you have a reliable clock. Omit it if you don't — the core module fills in server-now.

#### Example request

```json
{
  "entries": [
    {
      "endpoint": "/channels/123/messages",
      "method": "GET",
      "response_time_ms": 142.5,
      "status_code": 200,
      "db_queries": 3,
      "db_time_ms": 18.2,
      "timestamp": 1704067200
    },
    {
      "endpoint": "/users/@me",
      "method": "GET",
      "response_time_ms": 45.0,
      "status_code": 200
    }
  ]
}
```

The server maps entries to the core format before storing and leaves out `timestamp` when it's `null` so the core can default it (`plexichat/src/api/routes/telemetry.py:115`).

### Response

`200 OK` (`TelemetryResponse`)

```json
{
  "accepted": 2,
  "message": "Successfully accepted 2 telemetry entries"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `accepted` | integer | Number of entries accepted. |
| `message` | string | Human-readable status. |

When telemetry is disabled or not initialized, the server returns:

```json
{
  "accepted": 0,
  "message": "Telemetry module not available"
}
```

Failures during recording are logged with `exc_info` but the response still returns `200` with `accepted: 0` — telemetry delivery is best-effort and doesn't break the client app (`plexichat/src/api/routes/telemetry.py:139`).

### Errors

| Status | Condition |
|--------|-----------|
| `422` | Validation error (for example, more than 100 entries or `response_time_ms < 0`). |
| `429` | Rate limit exceeded (`telemetry submission rate limit exceeded`). |
| `500` | Never returned directly — the handler catches exceptions and returns `200` with `accepted: 0`. |

### Cardinality and retention

- `telemetry.max_distinct_endpoints` (default 500) caps how many distinct endpoint strings are tracked.
- `telemetry.max_total_rows` (default 100,000) caps total stored rows.
- `telemetry.percentile_sample_rows` (default 25,000) caps percentile calculations.
- `telemetry.retention_days` (default 30) controls the periodic cleanup sweep (`plexichat/src/api/app.py:151`).

## Submit CSP violation report

```
POST /api/v1/telemetry/csp-report
```

Handles browser-sent Content Security Policy violation reports. The endpoint is unauthenticated — browsers post directly with `Content-Type: application/csp-report` or `application/json`.

### Request body

Any JSON object. The server reads the raw body with a hard cap because `Content-Length` can be missing or forged with chunked encoding (`plexichat/src/api/routes/telemetry.py:192`).

| Constraint | Value |
|------------|-------|
| Max body size | 64 KiB (`_MAX_CSP_REPORT_BYTES` in `plexichat/src/api/routes/telemetry.py:25`) |
| Log cap | 4,096 characters (`_MAX_CSP_LOG_CHARS`) |
| Per-string cap in logs | 512 characters (`_MAX_CSP_URI_CHARS`) |

If the body exceeds 64 KiB, the server returns `413 CSP report exceeds the maximum size`.

If the body isn't valid JSON, it's stored as `{"raw": "<decoded body>"}`.

### Redaction and logging

Before logging, the server redacts attacker-controlled values (`plexichat/src/api/routes/telemetry.py:153`):

- Any field whose key ends with `-uri` or equals `referrer` has its query string stripped and is truncated to 512 characters (`_redact_uri`).
- All string values are truncated to 512 characters.
- The JSON dump of the sanitized report is truncated to 4,096 characters before it reaches the logger.

The log line is:

```
CSP report received from <ip>: <sanitized json>
```

The server also forwards the unsanitized report and client IP to `telemetry.record_csp_violation(report, client_ip)` when the telemetry module is enabled and that method exists. If the module is disabled, the report is only logged.

### Response

`204 No Content` — always, even when the report was malformed or telemetry is disabled. The handler catches all exceptions and logs them at `ERROR` without returning a failure to the browser.

### Example

Browser send (automatic):

```http
POST /api/v1/telemetry/csp-report HTTP/1.1
Content-Type: application/csp-report

{
  "csp-report": {
    "document-uri": "https://app.plexichat.com/settings?token=secret",
    "referrer": "https://app.plexichat.com/",
    "blocked-uri": "https://evil.example.com/script.js?x=1",
    "violated-directive": "script-src 'self'"
  }
}
```

Logged (redacted):

```json
{
  "csp-report": {
    "document-uri": "https://app.plexichat.com/settings",
    "referrer": "https://app.plexichat.com/",
    "blocked-uri": "https://evil.example.com/script.js",
    "violated-directive": "script-src 'self'"
  }
}
```

Query strings are removed so tokens embedded in URLs don't reach persistent logs.

## Client guidance

- Send only the fields documented above. Extra fields are ignored.
- Don't include secrets, tokens, or message content in telemetry.
- Treat telemetry delivery as best-effort and non-blocking. The client should not retry on failure.
- For response times, batch measurements and send them periodically rather than on every request.
- For CSP reports, you don't need to call this directly — the browser does when a policy is violated. Make sure your `Content-Security-Policy` header includes `report-uri /api/v1/telemetry/csp-report`.
