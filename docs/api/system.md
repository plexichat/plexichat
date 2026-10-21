# System and utility routes

Public utility endpoints for monitoring, version negotiation, feature discovery, help pages, and QR generation.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All paths in this document are prefixed with `/api/v1/` unless noted. Routes marked *unauthenticated* don't require a token.

## Endpoints at a glance

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/` | No | API root |
| `GET` | `/health` | No | Deep health check |
| `GET` | `/ready` | No | Readiness probe |
| `GET` | `/version` | No | Server version |
| `POST` | `/version/negotiate` | No | Version compatibility check |
| `GET` | `/status` | No | Operational state and uptime |
| `GET` | `/capabilities/artifacts` | Yes | Artifact feature capabilities |
| `GET` | `/help/security-logout` | No | Help page for forced logouts |
| `GET` | `/help/access-blocked` | No | Help page for IP blocks |
| `GET` | `/qr` | No | Generate a QR code image |
| `GET` | `/robots.txt` | No | Robots file (mounted at the app root, not under `/api/v1`) |

## Root

```
GET /
```

Returns basic API metadata. Mounted at the app root (`plexichat/src/api/app.py:544`), not under `/api/v1`.

**Response** `200 OK`

```json
{
  "name": "Plexichat API",
  "version": "a.1.0-142",
  "docs": "/docs",
  "api": "/api/v1",
  "api_docs": "/docs/api"
}
```

`api_docs` is present only when docs are enabled (`docs.enabled` in `plexichat/src/config_defaults.py:754`).

## Health checks

### Deep health check

```
GET /api/v1/health
```

Checks API health and returns advisory diagnostics. This endpoint never takes down the health check if optional subsystems fail — each metric is wrapped in `try/except` and logged at `DEBUG` (`plexichat/src/api/routes/health.py:46`).

**Response** `200 OK` (`HealthResponse`)

```json
{
  "status": "healthy",
  "version": "a.1.0-142",
  "events_metrics": {
    "dropped_count": 0,
    "subscriber_count": 12,
    "queue_size": 0,
    "max_queue_size": 1000
  },
  "is_queue_recreate_pending": false,
  "cross_worker_listener": {
    "status": "connected"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `healthy` when the endpoint is reachable. |
| `version` | string | Server version from `utils.version.current_string()`. Falls back to `unknown`. |
| `events_metrics` | object? | Loss metrics from `event_manager.get_loss_metrics()` (dropped events, subscribers, queue size). `null` during cold boot before `events.setup`. |
| `is_queue_recreate_pending` | boolean? | Whether the dispatch queue needs recreation after a reload. |
| `cross_worker_listener` | object? | Valkey pub/sub listener status from `cross_worker_listener_status()`. |

If the endpoint itself fails, it returns `500 Internal server error`.

### Readiness probe

```
GET /api/v1/ready
```

Minimal probe for load balancers and container orchestrators. It returns as soon as the HTTP server is bound and doesn't check the database or Valkey (`plexichat/src/api/routes/health.py:18`).

**Response** `200 OK`

```json
{
  "status": "ready"
}
```

Use `/ready` for routing decisions and `/health` for diagnostics.

## Version and compatibility

### Get server version

```
GET /api/v1/version
```

Returns the current version and the minimum supported client version. Cached for one hour (`@cached(ttl=3600)` in `plexichat/src/api/routes/version.py:67`).

**Response** `200 OK` (`ServerVersionResponse`)

```json
{
  "version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 133,
    "string": "a.1.0-142"
  },
  "min_supported_version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 100,
    "string": "a.1.0-100"
  },
  "api_version": "v1"
}
```

### Negotiate version

```
POST /api/v1/version/negotiate
```

Check whether a client version is compatible with the server.

**Request body** (`VersionNegotiateRequest`)

```json
{
  "client_version": "a.1.0-125"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_version` | string | Yes | Client version string. Must parse with `utils.version.parse`. |

**Compatibility logic** (`plexichat/src/api/routes/version.py:104`):

1. If the version doesn't parse, the server returns `400 INVALID_VERSION_FORMAT`.
2. If the version is older than `min_supported`, the server returns `426 VERSION_OUTDATED` with `update_required: true`.
3. If the version is compatible but older than the server, `update_recommended` is `true`.
4. Otherwise the client is fully compatible.

**Success response** `200 OK` (`VersionNegotiateResponse`)

```json
{
  "compatible": true,
  "server_version": { "stage": "a", "major": 1, "minor": 0, "build": 133, "string": "a.1.0-142" },
  "client_version": { "stage": "a", "major": 1, "minor": 0, "build": 125, "string": "a.1.0-125" },
  "min_supported_version": { "stage": "a", "major": 1, "minor": 0, "build": 100, "string": "a.1.0-100" },
  "update_required": false,
  "update_recommended": true,
  "update_url": null,
  "message": "A newer version (a.1.0-142) is available. Consider updating for the latest features and fixes."
}
```

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| `400` | `INVALID_VERSION_FORMAT` | `client_version` can't be parsed. |
| `426` | `VERSION_OUTDATED` | Client is below `min_supported`. The body includes `min_version`, `server_version`, and `update_url`. |
| `500` | `INTERNAL_SERVER_ERROR` | Unexpected failure. |

### Get server status

```
GET /api/v1/status
```

Returns operational state, version, uptime, and any maintenance announcement. Cached for 10 seconds (`plexichat/src/api/routes/version.py:231`).

**Response** `200 OK` (`ServerStatusResponse`)

```json
{
  "state": "running",
  "version": { "stage": "a", "major": 1, "minor": 0, "build": 133, "string": "a.1.0-142" },
  "uptime_seconds": 86400,
  "maintenance_message": null,
  "estimated_downtime_seconds": null,
  "restart_at": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `state` | string | Server state (`running`, `maintenance`, etc. from `ServerState`). Updated via `set_server_state` |
| `version` | object | Current version info. |
| `uptime_seconds` | integer | Seconds since the server started. |
| `maintenance_message` | string? | Human-readable maintenance notice. |
| `estimated_downtime_seconds` | integer? | Estimated downtime when not running normally. |
| `restart_at` | string? | ISO timestamp of a scheduled restart. |

Poll `GET /status` every 60 seconds during normal operation and every 5 seconds when `state` isn't `running`.

## Capabilities

```
GET /api/v1/capabilities/artifacts
```

Returns per-feature availability for artifacts. Use this instead of hardcoding feature flags.

- **Auth**: Requires `Authorization: Bearer <token>` (`get_current_user` in `plexichat/src/api/routes/capabilities.py:32`).
- **Prefix**: `/capabilities/artifacts` (`plexichat/src/api/routes/__init__.py:62` — the sub-router is mounted without an extra `/api/v1` prefix duplication; the full path is `/api/v1/capabilities/artifacts`).

**Response** `200 OK` — map of feature to capability object:

```json
{
  "editor": { "available": true, "reason": null },
  "whiteboard": { "available": false, "reason": "licensed_feature_missing" },
  "voice_transcription": { "available": true, "reason": null }
}
```

The server builds this from `get_artifact_capabilities()` and `capability_to_dict` in `plexichat/src/core/artifacts/capabilities.py:15`.

**Errors**:

| Status | Condition |
|--------|-----------|
| `401` | Missing or invalid token. |
| `500` | Failed to compute capabilities (details logged). |

## Help pages

These endpoints return HTML, not JSON. They're meant for human-facing guidance linked from clients.

### Security logout help

```
GET /api/v1/help/security-logout
```

Explains forced-logout reasons (admin invalidation, session reset, policy change).

- **Response**: `200 OK` with `text/html`. Rendered from `_get_help_style` in `plexichat/src/api/routes/help.py:61`.

### Access blocked help

```
GET /api/v1/help/access-blocked
```

Explains IP blocks (terms violations, abuse detection, manual block).

- **Response**: `200 OK` with `text/html`.

### Robots file

```
GET /robots.txt
```

Serves `static/robots.txt` from the working directory. Mounted at the app root without the `/api/v1` prefix and excluded from the OpenAPI schema (`plexichat/src/api/routes/help.py:12`).

## QR code generation

```
GET /api/v1/qr
GET /qr
```

Generates a QR code image locally (no external service). Both paths are valid — the router is mounted at `/qr` in `plexichat/src/api/routes/__init__.py:103` and `/api/v1/qr` via the `api_prefix`.

### Query parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `data` | string | Yes | Content to encode. Empty values return `400`. |
| `size` | string | No | Image size as `WIDTHxHEIGHT`, for example `200x200`. Defaults to `200x200`. Values are clamped to `10–1000` per axis. Malformed sizes fall back to `200x200`. |
| `format` | string | No | `png` (default) or `jpg`. Any non-`png` value produces JPEG. |

### Behavior

- If `qrcode` isn't installed, the server returns `500 QR code dependency missing`.
- Image generation runs in a thread pool (`run_in_threadpool`) so the event loop isn't blocked.
- Successful responses include `Cache-Control: public, max-age=86400` and `Access-Control-Allow-Origin: *`.

### Response

`200 OK` with `image/png` or `image/jpeg` bytes.

```bash
curl "http://localhost:8000/api/v1/qr?data=hello&size=300x300&format=png" --output qr.png
```

### Errors

| Status | Condition |
|--------|-----------|
| `400` | Missing or empty `data`. |
| `500` | Missing `qrcode` dependency or unexpected generation failure. |

## Client guidance

- Call `GET /version` and `POST /version/negotiate` before enabling client features that depend on a minimum server version.
- Use `GET /capabilities/artifacts` for runtime feature hints. Don't hardcode artifact availability.
- Use `GET /status` and `GET /health` for diagnostics. Use `GET /ready` for load-balancer health checks.
- Treat help endpoints as supplemental UI — don't parse them as structured API data.
- For QR codes, pass `data` and optionally `size` and `format`. Don't send secrets in the query string that you don't want in server logs.
