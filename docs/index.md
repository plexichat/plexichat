# PlexiChat API Documentation

Welcome to the PlexiChat API documentation. PlexiChat is a real-time messaging platform with a REST API for resource management and a WebSocket gateway for real-time events.

## Quick Navigation

| Section | Description |
|---------|-------------|
| [Getting Started](getting-started.md) | Authentication, setup, and first API call |
| [Configuration](configuration.md) | Server configuration options |
| [REST API Reference](api/index.md) | Complete endpoint documentation |
| [WebSocket Gateway](websocket/index.md) | Real-time events and connection handling |
| [Rate Limits](rate-limits.md) | Rate limiting policies |
| [Error Handling](errors.md) | Error codes and responses |
| [Data Types](data-types.md) | Common data formats |

## Base URLs

| Service | Development | Production |
|---------|-------------|------------|
| REST API | `http://localhost:8000/api/v1` | Configure in `config.yaml` |
| WebSocket Gateway | `ws://localhost:8000/gateway` | Configure in `config.yaml` |
| Interactive Docs | `http://localhost:8000/docs` | Disabled in production |

## Authentication

All authenticated endpoints require a token in the `Authorization` header:

```http
Authorization: Bearer <session_token>
```

For bot applications:

```http
Authorization: Bot <bot_token>
```

See [Authentication](api/authentication.md) for complete details.

## API Features

- RESTful JSON API with consistent error handling
- WebSocket gateway for real-time events
- Snowflake IDs for all resources
- Cursor-based pagination
- Rate limiting with headers
- Two-factor authentication support
- User presence and status
- Server/guild management
- Direct messages and group conversations
- Message reactions and embeds
- File attachments and media
- Webhooks for integrations
- User settings sync

## Version Information

- API Version: `v1`
- Current Server Version: Check `/api/v1/version`

## Data Storage

Default data location: `~/.plexichat/`

| Directory | Contents |
|-----------|----------|
| `data/` | Database files |
| `logs/` | Application logs |
| `media/` | Uploaded files and attachments |
| `config/` | Configuration files |

## Related Documentation

- [API Reference](api/index.md) - All REST endpoints
- [WebSocket Events](websocket/events.md) - Real-time event types
- [Performance Guide](performance.md) - Optimization tips
