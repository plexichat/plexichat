# Plexichat backend documentation

This portal documents the Plexichat backend: the REST API, the WebSocket gateway, common response shapes, and integration guidance.

## Documentation surfaces

*   **Narrative docs** (this portal) — Guides, configuration, and reference at `/docs/api`.
*   **OpenAPI explorer** — Interactive request builder at `/docs`.
*   **API reference (ReDoc)** — Readable schema reference at `/redoc`.
*   **Schema JSON** — Raw OpenAPI spec at `/openapi.json`.

The docs path is configurable through `docs.path` (default `/docs/api`) and `api.docs_url` (default `/docs`).

## Quick links

### Get started

*   [Get started](getting-started.md) — Authenticate, make your first API requests, and connect to the WebSocket gateway.
*   [Features](features.md) — Overview of all Plexichat feature areas.
*   [Data types](data-types.md) — Snowflake IDs, timestamps, and common data formats.
*   [Permissions](end-user/permissions.md) — Permission categories and individual permission reference.

### Configuration

*   [Configuration overview](configuration.md) — How config files are discovered and loaded.
*   [Default configuration reference](default-config.md) — Complete config reference with all defaults from `src/config_defaults.py`.
*   [Authentication](deployment/configuration/config-authentication.md) — Password policies, sessions, 2FA, OAuth, account deletion, and age gate.
*   [Database](deployment/configuration/config-database.md) — PostgreSQL and SQLite setup, connection pooling, and migrations.
*   [Valkey](deployment/configuration/config-valkey.md) — Caching, session storage, and connection pooling.
*   [Media](deployment/configuration/config-media.md) — File uploads, storage backends, processing, and security.
*   [Voice](deployment/configuration/config-voice.md) — WebRTC signaling, SFU backends, and STUN/TURN.
*   [WebSocket](deployment/configuration/config-websocket.md) — Gateway settings, compression, rate limits, and allowed origins.
*   [Search](deployment/configuration/config-search.md) — Search backends, indexing, and result limits.
*   [Rate limiting](deployment/configuration/config-rate-limiting.md) — Global, user, IP, bot, and webhook rate limits. Defaults are defined in `src/config_defaults.py:270`.
*   [API and server](deployment/configuration/config-api.md) — CORS, trusted proxies, debug mode, and TLS.
*   [Email](deployment/configuration/config-email.md) — SMTP setup for verification and password reset.
*   [Embeds and URL preview](deployment/configuration/config-embeds.md) — Embed limits and URL preview configuration.
*   [Static client](deployment/configuration/config-static-client.md) — Serve the Vite build from the backend with GitLab releases and SHA-256 verification.

### Licensing

*   [Licensing](deployment/licensing.md) — License file setup, environment variable configuration, hot-swapping, and feature gating.

### Deployment and operations

*   [Deployment](deployment/index.md) — Installation, security hardening, scaling, monitoring, and backup.
*   **[Docker deployment](deployment/docker/index.md)** — Quick start, configuration, development, production setup, and troubleshooting.
*   [Versioning and updates](deployment/versioning.md) — Version scheme `a.1.0-158`, update procedures, and rollback strategies.
*   [SQLite to PostgreSQL migration](./deployment/postgres-migration.md) — Automated migration guide.
*   [Security best practices](security.md) — Authentication security, encryption, and production checklist.
*   [Performance tuning](performance.md) — Subsystem optimization and scaling recommendations.
*   [Access tokens](admin/index.md#access-tokens) — Optional API access-token gate for closed deployments.
*   [Data export (DSAR)](admin/data-export.md) — GDPR Article 20 right-to-portability exports.
*   [Rate limits](rate-limits.md) — Rate limit tiers and `429` response handling.
*   [Errors](errors.md) — Error response format and common error codes.
*   [OAuth scopes](oauth-scopes.md) — OAuth scope reference for bot and application permissions.

### API and WebSocket reference

*   [API reference](api/index.md) — Route-group overviews for every API module.
*   [WebSocket gateway](websocket/index.md) — Connection flow, events, intents, and opcodes.

### End-user and developer guides

*   [End-user guide](end-user/index.md) — Guide for regular users.
*   [Client development](client-development/index.md) — Guide for client, bot, and integration developers.

## Runtime endpoints

| Endpoint | Method | Description |
| --- | --- | --- |
| `{{BASE_URL}}` | — | REST API base (default `http://localhost:8000/api/v1`). |
| `{{WEBSOCKET_URL}}` | — | WebSocket gateway (default `ws://localhost:8000/gateway`). |
| `/api/v1/ready` | `GET` | Readiness probe. Returns as soon as HTTP is bound. |
| `/api/v1/health` | `GET` | Health check. Covers database, events, and cross-worker listener. |
| `/api/v1/version` | `GET` | Current server version and minimum supported client version. |
| `/api/v1/version/negotiate` | `POST` | Version compatibility check for a given `client_version`. |
| `/api/v1/status` | `GET` | Server state, version, and uptime. |
| `/api/v1/capabilities` | `GET` | Public constants without authentication. |

Version endpoints are implemented in `src/api/routes/version.py:64` and use the version utility in `src/utils/common_utils/utils/version/__init__.py:1`.
