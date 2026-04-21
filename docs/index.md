# Plexichat Backend Documentation

This portal documents the Plexichat backend: the REST API, the WebSocket gateway, common response shapes, and integration guidance.

## Documentation Surfaces

- Narrative Docs (this portal) -- guides, configuration, and reference at `/docs/api`
- OpenAPI Explorer -- interactive request builder at `/docs`
- API Reference (ReDoc) -- readable schema reference at `/redoc`
- Schema JSON -- raw OpenAPI spec at `/openapi.json`

## Quick Links

### Getting Started

- [Getting Started](getting-started.md) -- authenticate, make your first API requests, and connect to the WebSocket gateway
- [Features](features.md) -- overview of all Plexichat feature areas
- [Data Types](data-types.md) -- Snowflake IDs, timestamps, and common data formats
- [Permissions](permissions.md) -- permission categories and individual permission reference

### Configuration

- [Configuration Overview](configuration.md) -- how config files are discovered and loaded
- [Default Configuration Reference](default-config.md) -- complete config reference with all defaults
- [Authentication](config-authentication.md) -- password policies, sessions, 2FA, OAuth, account deletion, age gate
- [Database](config-database.md) -- PostgreSQL/SQLite setup, connection pooling, migrations
- [Redis](config-redis.md) -- caching, session storage, connection pooling
- [Media](config-media.md) -- file uploads, storage backends, processing, security
- [Voice](config-voice.md) -- WebRTC signaling, SFU backends, STUN/TURN
- [WebSocket](config-websocket.md) -- gateway settings, compression, rate limits, origins
- [Search](config-search.md) -- search backends, indexing, result limits
- [Rate Limiting](config-rate-limiting.md) -- global, user, IP, bot, and webhook rate limits
- [API & Server](config-api.md) -- CORS, trusted proxies, debug mode, TLS
- [Email](config-email.md) -- SMTP setup for email verification and password reset
- [Embeds & URL Preview](config-embeds.md) -- embed limits and URL preview configuration

### Deployment & Operations

- [Deployment](deployment.md) -- installation, security hardening, scaling, monitoring, backup
- [Security Best Practices](security.md) -- authentication security, encryption, production checklist
- [Performance Tuning](performance.md) -- subsystem optimization and scaling recommendations
- [Access Tokens](admin-access-tokens.md) -- optional API access-token gate for closed deployments
- [Rate Limits](rate-limits.md) -- rate limit tiers and 429 response handling
- [Errors](errors.md) -- error response format and common error codes
- [OAuth Scopes](oauth-scopes.md) -- OAuth scope reference for bot and application permissions

### API & WebSocket Reference

- [API Reference](api/index.md) -- route-group overviews for every API module
- [WebSocket Gateway](websocket/index.md) -- connection flow, events, intents, and opcodes

### End-User & Developer Guides

- [End-User Guide](end-user/index.md) -- getting started for regular users
- [Client Development](client-development/index.md) -- guide for client, bot, and integration developers

## Runtime Endpoints

- REST API base: `{{BASE_URL}}`
- WebSocket gateway: `{{WEBSOCKET_URL}}`
- Health check: `/health`
- Version endpoints: `/api/v1/version`, `/api/v1/version/negotiate`, `/api/v1/status`
