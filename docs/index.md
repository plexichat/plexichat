# Plexichat Backend Documentation

This portal documents the Plexichat backend runtime surface: the REST API, the WebSocket gateway, common response shapes, and integration guidance.

## Documentation Surfaces

- **Narrative Docs** (this portal) — Guides, configuration, and reference at `/docs/api`
- **OpenAPI Explorer** — Interactive request builder at `/docs`
- **API Reference (ReDoc)** — Readable schema reference at `/redoc`
- **Schema JSON** — Raw OpenAPI spec at `/openapi.json`

## Quick Links

### Getting Started

- [Getting Started](getting-started.md) — Authenticate, make your first API requests, and connect to the WebSocket gateway
- [Features](features.md) — Overview of all Plexichat feature areas
- [Data Types](data-types.md) — Snowflake IDs, timestamps, and common data formats
- [Permissions](permissions.md) — Permission categories and individual permission reference

### Configuration

- [Configuration Overview](configuration.md) — How config files are discovered and loaded
- [Default Configuration Reference](default-config.md) — Complete config reference with all defaults
- [Authentication](config-authentication.md) — Password policies, sessions, 2FA, OAuth, account deletion
- [Database](config-database.md) — PostgreSQL/SQLite setup, connection pooling, migrations
- [Redis](config-redis.md) — Caching, session storage, connection pooling
- [Media](config-media.md) — File uploads, storage backends, processing, security
- [Voice](config-voice.md) — WebRTC signaling, SFU backends, STUN/TURN
- [WebSocket](config-websocket.md) — Gateway settings, compression, rate limits, origins
- [Search](config-search.md) — Search backends, indexing, result limits
- [Rate Limiting](config-rate-limiting.md) — Global, user, IP, bot, and webhook rate limits
- [API & Server](config-api.md) — CORS, trusted proxies, debug mode, TLS

### Deployment & Operations

- [Deployment](deployment.md) — Installation, security hardening, scaling, monitoring, backup
- [Security Best Practices](security.md) — Authentication security, encryption, production checklist
- [Performance Tuning](performance.md) — Subsystem optimization and scaling recommendations
- [Access Tokens](admin-access-tokens.md) — Optional API access-token gate for closed deployments
- [Rate Limits](rate-limits.md) — Rate limit tiers and 429 response handling
- [Errors](errors.md) — Error response format and common error codes
- [OAuth Scopes](oauth-scopes.md) — OAuth scope reference for bot and application permissions

### API & WebSocket Reference

- [API Reference](api/index.md) — Route-group overviews for every API module
- [WebSocket Gateway](websocket/index.md) — Connection flow, events, intents, and opcodes

### End-User & Developer Guides

- [End-User Guide](end-user/index.md) — Getting started for regular users
- [Client Development](client-development/index.md) — Guide for client, bot, and integration developers

## Runtime Endpoints

- REST API base: `{{BASE_URL}}`
- WebSocket gateway: `{{WEBSOCKET_URL}}`
- Health check: `/health`
- Version endpoints: `/api/v1/version`, `/api/v1/version/negotiate`, `/api/v1/status`
