# PlexiChat API Documentation

Welcome to the PlexiChat API documentation. This guide provides comprehensive information about the PlexiChat REST API and WebSocket Gateway.

## Overview

PlexiChat is a real-time messaging platform with a REST API for resource management and a WebSocket gateway for real-time events.

## Quick Links

- [Getting Started](getting-started.md) - Authentication and first API call
- [REST API Reference](api/index.md) - Complete endpoint documentation
- [WebSocket Gateway](websocket/index.md) - Real-time events
- [Rate Limits](rate-limits.md) - Rate limiting information
- [Error Handling](errors.md) - Error codes and handling
- [Data Types](data-types.md) - Common data types and formats

## Base URLs

| Service | URL |
|---------|-----|
| REST API | `https://api.example.com/api/v1` |
| WebSocket Gateway | `wss://gateway.example.com/gateway` |

## Current Version

- API Version: `v1`
- Server Version: `a.1.0-1`

## Authentication

All authenticated endpoints require a token in the Authorization header:

```
Authorization: Bearer <session_token>
Authorization: Bot <bot_token>
```

## Support

For API support and questions, please refer to the official PlexiChat documentation or contact support.
