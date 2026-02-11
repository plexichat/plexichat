# Welcome to PlexiChat Documentation

PlexiChat is a high-performance, secure, and modern real-time messaging platform. This documentation portal provides everything you need to deploy, configure, and integrate with PlexiChat.

## Quick Links

- [Getting Started](/getting-started) - Setup your server in minutes.
- [Configuration](/configuration) - Fine-tune every aspect of your deployment.
- [Deployment Guide](/deployment) - Best practices for production environments.
- [API Reference](/api) - Detailed documentation for all REST endpoints.
- [WebSocket Gateway](/websocket) - Real-time event system and opcodes.
- [Features](/features) - Complete list of all PlexiChat features.

## Platform Features

- **High Performance**: Built on FastAPI and asynchronous Python architecture.
- **Security First**: Zero-friction at-rest encryption and robust authentication.
- **Flexible Storage**: Support for local storage and S3-compatible backends.
- **Multi-Account**: Seamless support for human users and bot accounts.
- **Rich Messaging**: Markdown support, reactions, and multi-media attachments.
- **Voice & Video**: WebRTC support with Mediasoup or Janus SFU.
- **Moderation**: Auto-moderation with configurable rules and AI backend support.
- **Bot Platform**: OAuth2, slash commands, and interaction system.
- **Server Discovery**: Public server directory with categories.
- **Comprehensive Testing**: 3000+ tests with 85%+ coverage target.

## Project Structure

This workspace contains three primary components:

| Component | Description |
|-----------|-------------|
| plexichat/ | Core Server - FastAPI REST API and WebSocket gateway |
| plexichat-client/ | Web Client - Flask-based web interface |
| common-utils/ | Shared Utilities - Logging, config, and validation |

## API Base URL

The API base URL is dynamically determined based on your deployment:

```
{{BASE_URL}}
```

All API endpoints are relative to this base URL. For example:
- Authentication: `{{BASE_URL}}/auth/login`
- Get current user: `{{BASE_URL}}/users/@me`
- Send message: `{{BASE_URL}}/channels/{channel_id}/messages`

## WebSocket Gateway

The WebSocket gateway URL for real-time events:

```
{{WEBSOCKET_URL}}
```

## Community and Support

- **Bug Reports**: Use the /bug command in the CLI.
- **Feature Requests**: Open a ticket in the admin panel.
- **Documentation**: Managed within the repository for version consistency.
