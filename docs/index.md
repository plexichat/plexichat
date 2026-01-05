# Welcome to PlexiChat Documentation

PlexiChat is a high-performance, secure, and modern real-time messaging platform. This documentation portal provides everything you need to deploy, configure, and integrate with PlexiChat.

## Quick Links

- [Getting Started](/getting-started) - Setup your server in minutes.
- [Configuration](/configuration) - Fine-tune every aspect of your deployment.
- [Deployment Guide](/deployment) - Best practices for production environments.
- [API Reference](/reference) - Detailed documentation for all REST endpoints.
- [WebSocket Gateway](/websocket) - Real-time event system and opcodes.

## Platform Features

- **High Performance**: Built on FastAPI and asynchronous Python architecture.
- **Security First**: Zero-friction at-rest encryption and robust authentication.
- **Flexible Storage**: Support for local storage and S3-compatible backends.
- **Multi-Account**: Seamless support for human users and bot accounts.
- **Rich Messaging**: Markdown support, reactions, and multi-media attachments.

## Project Structure

This workspace contains three primary components:

| Component | Description |
|-----------|-------------|
| plexichat/ | Core Server - FastAPI REST API and WebSocket gateway |
| plexichat-client/ | Web Client - Flask-based web interface |
| common-utils/ | Shared Utilities - Logging, config, and validation |

## Community and Support

- **Bug Reports**: Use the /bug command in the CLI.
- **Feature Requests**: Open a ticket in the admin panel.
- **Documentation**: Managed within the repository for version consistency.