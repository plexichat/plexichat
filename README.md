# PlexiChat Server

A real-time messaging platform server with REST API and WebSocket gateway.

## Installation

```bash
# Clone with submodules (required)
git clone --recurse-submodules https://gitlab.com/plexichat/plexichat.git

# Or if already cloned without submodules:
git submodule update --init --recursive
```

## Features

- REST API for user management, servers, channels, and messages
- WebSocket gateway for real-time events
- User authentication with 2FA support
- Server and channel management
- Direct messaging and group conversations
- Friend relationships and blocking
- Presence and typing indicators
- File attachments and media uploads
- User avatars and server icons (database-stored)
- Reactions and embeds
- Webhooks
- Rate limiting
- SQLite or PostgreSQL database support (automatic schema type conversion)
- Optional Redis for caching and sessions
- S3/MinIO compatible media storage

## Quick Start

```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/Mac)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run server
python main.py
```

The server will:

- Store data in `~/.plexichat/` (home folder)
- Start on `http://localhost:8000`
- API available at `http://localhost:8000/api/v1`
- Docs at `http://localhost:8000/docs`

## Configuration

Configuration is loaded from (in order):

1. `config/config.yaml`
2. `~/.plexichat/config/config.yaml`

See the root README.md for full configuration options.

## Project Structure

```
plexichat/
├── main.py              # Entry point
├── conftest.py          # Pytest configuration
├── requirements.txt     # Production dependencies
├── requirements-test.txt # Test dependencies
├── config/              # Default configuration
├── src/
│   ├── api/             # FastAPI application
│   │   ├── routes/      # API endpoints
│   │   ├── schemas/     # Request/response models
│   │   ├── middleware/  # Auth, rate limiting, etc.
│   │   └── websocket/   # Gateway implementation
│   ├── core/            # Business logic modules
│   │   ├── auth/        # Authentication & sessions
│   │   ├── messaging/   # Messages & conversations
│   │   ├── servers/     # Servers & channels
│   │   ├── presence/    # Online status & typing
│   │   ├── relationships/ # Friends & blocking
│   │   ├── reactions/   # Message reactions
│   │   ├── webhooks/    # Webhook management
│   │   └── ...          # Other modules
│   ├── tests/           # Test suite
│   └── utils/           # Shared utilities
└── docs/                # Documentation
```

## API Overview

### Authentication

- `POST /api/v1/auth/register` - Register new user
- `POST /api/v1/auth/login` - Login
- `POST /api/v1/auth/2fa` - Complete 2FA
- `POST /api/v1/auth/logout` - Logout

### Users

- `GET /api/v1/users/@me` - Get current user
- `PATCH /api/v1/users/@me` - Update profile

### Servers & Channels

- `GET /api/v1/servers` - List servers
- `POST /api/v1/servers` - Create server
- `GET /api/v1/channels/{id}/messages` - Get messages
- `POST /api/v1/channels/{id}/messages` - Send message

### Avatars

- `GET /api/v1/avatars/users/{id}` - Get user avatar (public)
- `POST /api/v1/avatars/users/@me` - Upload user avatar
- `GET /api/v1/avatars/servers/{id}` - Get server icon (public)
- `POST /api/v1/avatars/servers/{id}` - Upload server icon

### WebSocket Gateway

Connect to `ws://localhost:8000/gateway` for real-time events.

See `docs/` for full API documentation.

## Admin Panel

Built-in admin panel for managing feedback and viewing telemetry.

- Access at `http://localhost:8000/api/v1/admin`
- Credentials auto-generated on first run (saved to `~/.plexichat/admin_credentials.txt`)
- 2FA required by default (configurable via `admin_ui.require_otp`)
- Host-restricted to localhost by default

See root README.md for full configuration options.

## Testing

```bash
pip install -r requirements-test.txt
pytest -v
```

## Version

Current version: `a.1.0-23` (Alpha)
