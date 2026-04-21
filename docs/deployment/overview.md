# Deployment Overview

Plexichat is a distributed messaging platform that can be deployed in various environments from development to production. This guide covers the complete deployment process.

## Deployment Options

Plexichat supports multiple deployment strategies:
- **Development**: Local setup with SQLite database
- **Production**: PostgreSQL with Redis caching and external storage
- **Containerized**: Docker/Kubernetes deployments (community-supported)
- **Cloud**: Various cloud provider options

## Key Components

1. **Backend Server** (`plexichat`): Python/FastAPI application providing REST API and WebSocket gateway
2. **Client Interface** (`plexichat-client`): Python/Flask web application serving the frontend
3. **Shared Utilities** (`common-utils`): Common functionality used by both server and client
4. **Database**: PostgreSQL (recommended) or SQLite (development only)
5. **Cache**: Redis (recommended for production)
6. **Storage**: Local filesystem or S3-compatible (MinIO, AWS S3, etc.) for media attachments

## Prerequisites

Before deploying Plexichat, ensure you have:
- Git (for cloning repositories)
- Python 3.11+ (for both server and client)
- pip (Python package manager)
- Node.js 16+ (only needed for client testing with Playwright)
- PostgreSQL 12+ (for production deployments)
- Redis 6+ (recommended for production)

## Deployment Flow

1. Clone the repositories from GitLab
2. Install dependencies for both server and client
3. Configure environment variables and configuration files
4. Initialize the database (runs automatically on first startup)
5. Start the services
6. Verify deployment through health check endpoints

For system requirements, see [Requirements](requirements.md). For configuration, see the [Configuration Overview](../configuration.md) and [Default Configuration Reference](../default-config.md).