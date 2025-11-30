# PlexiChat API Documentation

Welcome to the PlexiChat API documentation. This guide provides comprehensive information about the PlexiChat REST API and WebSocket Gateway.

## Overview

PlexiChat is a real-time messaging platform with a REST API for resource management and a WebSocket gateway for real-time events.

## Quick Links

- [Getting Started](getting-started.md) - Authentication and first API call
- [Configuration](configuration.md) - Server configuration guide
- [REST API Reference](api/index.md) - Complete endpoint documentation
- [WebSocket Gateway](websocket/index.md) - Real-time events
- [Rate Limits](rate-limits.md) - Rate limiting information
- [Error Handling](errors.md) - Error codes and handling
- [Data Types](data-types.md) - Common data types and formats

## Base URLs

| Service | URL (Development) | URL (Production) |
|---------|-------------------|------------------|
| REST API | `http://localhost:8000/api/v1` | Configure in `config.yaml` |
| WebSocket Gateway | `ws://localhost:8000/gateway` | Configure in `config.yaml` |
| Swagger Docs | `http://localhost:8000/docs` | - |
| ReDoc | `http://localhost:8000/redoc` | - |

## Current Version

- API Version: `v1`
- Server Version: `a.1.0-1`

## Data Storage

All data is stored in `~/.plexichat/` by default:
- `data/` - Database files
- `logs/` - Log files
- `media/` - Uploaded media
- `config/` - Configuration files

## Authentication

All authenticated endpoints require a token in the Authorization header:

```
Authorization: Bearer <session_token>
Authorization: Bot <bot_token>
```

## Support

For API support and questions, please refer to the official PlexiChat documentation or contact support.
