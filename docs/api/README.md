# Plexichat REST API

This directory contains narrative documentation for the Plexichat REST API. Use this documentation with the generated OpenAPI schema at `/docs` (Swagger) and `/openapi.json` for exact request and response shapes.

## Base URL

| Environment | Base URL |
|---|---|
| Production | `https://api.plexichat.com/api/v1` |
| Development | `http://localhost:8000/api/v1` |

The API prefix is configurable as `api.api_prefix` (default `/api/v1`) in `src/config_defaults.py:130`. The server host and port are `server.host` and `server.port` (`src/config_defaults.py:70`), not `api.host` or `api.port`.

All paths in the `docs/api/` guides are relative to the API prefix unless noted.

## Authentication

The API supports the following schemes:

- User session token: `Authorization: Bearer <token>`
- Bot token: `Authorization: Bot <token>`
- Admin session token for `<admin-base>` routes (default `/api/v1/admin`)

For details, see [Authentication](authentication.md), [Admin API](admin.md), and `src/api/middleware/authentication.py`.

## Rate limiting

Rate limiting is enabled by default (`rate_limiting.enabled`) and enforced in the application and optionally in Nginx. Do not rely on hard-coded values from older docs.

Real defaults are defined in `src/config_defaults.py:270`:

| Scope | Requests | Window | Burst | Notes |
|---|---|---|---|---|
| Global | 25000 | 60 seconds | 5000 | Shared across all clients |
| User | 10000 | 60 seconds | 2500 | Per authenticated user |
| IP | 5000 | 60 seconds | 1500 | Per IP address |
| Hourly (user) | 300000 | 3600 seconds | — | Rolling hourly cap |
| Daily (user) | 5000000 | 86400 seconds | — | Rolling daily cap |

Additional per-route limits apply (for example, `static_client_html` and `media` uploads). Update limits in the config file and in your reverse proxy when you change them.

Rate-limit headers are returned on `429` responses. Admin and internal requests may bypass limits when `rate_limiting.admin_bypass` or `rate_limiting.internal_bypass` is enabled.

## Real-time gateway

The WebSocket gateway is at `/gateway`, not `/ws`.

- Endpoint: `wss://api.plexichat.com/gateway` (dev: `ws://localhost:8000/gateway`)
- Implementation: `src/api/websocket/gateway.py` (`gateway_endpoint`) (`@router.websocket("/gateway")`)
- Dev proxy: `vite.config.js` forwards `/api` and `/gateway` to `http://127.0.0.1:5411`

See [Gateway and WebSocket](../websocket/gateway.md) and [Opcodes](../websocket/opcodes.md). Required opcodes include `50 RATCHET_UPDATE`, `60-67 ARTIFACT_*` and `SUM_*`, and `68 ARTIFACT_OP_REJECTED` (`src/api/websocket/opcodes.py:40`).

## Health checks

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/ready` | Readiness probe for orchestrators. Returns `200` when the HTTP server is bound. No database checks. (`src/api/routes/health.py:18`) |
| `GET /api/v1/health` | Deep health check. Returns `status`, `version`, `events_metrics`, and `cross_worker_listener` status. May return `500` if the check itself fails. |

## Core reference pages

| Guide | Description |
|---|---|
| [Authentication](authentication.md) | Registration, login, sessions, 2FA, OAuth, and passkeys |
| [Users](users.md) | User profiles and settings |
| [Settings](settings.md) | User settings, including `PUT /settings/bulk` |
| [Servers](servers.md) | Servers, members, roles, and invites. Channel field is `channel_type` (`src/api/schemas/servers.py:85`) |
| [Channels](channels.md) | Channel management |
| [Messages](messages.md) | Messages, `read_by` as `ReaderInfo` objects (`src/api/schemas/messages.py:94`), `deleted`, `mentions`, and polls |
| [Reactions](reactions.md) | Message reactions |
| [Relationships](relationships.md) | Friends and blocks |
| [Presence](presence.md) | Presence updates. `GET /users/{id}/presence` returns `404` when not visible (`src/api/routes/presence.py:379`) |
| [Webhooks](webhooks.md) | Webhooks, including `PATCH /webhooks/{id}` and `POST /webhooks/{id}/regenerate` |
| [Avatars](avatars.md) | User avatars and server icons |
| [Emojis](emojis.md) | Custom emojis |
| [Features](features.md) | Bookmarks, forwarding, and scheduled messages (`/features`) |

## Additional route groups

| Guide | Description |
|---|---|
| [Search](search.md) | Full-text search |
| [Notifications](notifications.md) | Notifications |
| [Polls](polls.md) | Polls |
| [Voice](voice.md) | Voice and SFU. Default backend is `aiortc` (`src/config_defaults.py:396`), not `mediasoup` |
| [Media](media.md) | Uploads, `GET /media/proxy`, and `GET /api/v1/media/attachments/{filename}` |
| [Reports](reports.md) | User and message reports |
| [Feedback](feedback.md) | User feedback |
| [Telemetry](telemetry.md) | Client telemetry (opt-in) |
| [System](system.md) | System information |
| [Admin](admin.md) | Operator admin surface at `<admin-base>` |

## Conventions used in these guides

- All timestamps are integers (milliseconds since epoch) unless a schema specifies otherwise.
- Snowflake IDs are encoded as strings in JSON.
- Errors use the `ErrorResponse` model with `{ "error": { "code": <http-status>, "message": "<message>" } }`.
- Configuration keys are verified against `src/config_defaults.py`. Common corrections: use `server.host`/`server.port`/`server.reload`, `api.api_prefix`, `tls` (not `api.ssl`), and flat `cors_*` keys.
- Infrastructure versions: Postgres `18-alpine` and Python `3.14` (`docker-compose.yml:25` and `Dockerfile`). Valkey `9.1.1`.

## Generated reference

For exact schemas, use the live OpenAPI docs:

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

Keep environment-specific details (hosts, secrets, and deployment topology) out of these guides; put them in your deployment configuration and runbooks.
