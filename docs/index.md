# Plexichat Backend Documentation

This portal documents the Plexichat backend runtime surface: the REST API, the WebSocket gateway, common response shapes, and integration guidance.

## Documentation Surfaces

- [Getting Started](getting-started.md)
- [API Reference](api/index.md)
- [WebSocket Gateway](websocket/index.md)
- generated OpenAPI docs at `/docs`
- raw schema at `/openapi.json`

## Runtime Endpoints

- REST API base: `{{BASE_URL}}`
- WebSocket gateway: `{{WEBSOCKET_URL}}`
- Health check: `/health`
- Version endpoints: `/api/v1/version`, `/api/v1/version/negotiate`, `/api/v1/status`

## What This Portal Covers

- authentication, sessions, and capability discovery
- users, relationships, servers, channels, and messages
- reactions, presence, notifications, polls, voice, media, and reports
- shared behaviors such as rate limits, common errors, and WebSocket lifecycle rules



## Suggested Reading Order

1. [Getting Started](getting-started.md)
2. [Configuration](configuration.md)
3. [API Reference](api/index.md)
4. [WebSocket Gateway](websocket/index.md)
5. [Rate Limits](rate-limits.md)
6. [Security](security.md)
