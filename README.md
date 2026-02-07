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

- **REST API**: Comprehensive FastAPI-based API for all messaging features
- **Real-time Gateway**: WebSocket-based event delivery system
- **Advanced Messaging**:
  - Direct messaging, group chats, and server channels
  - Thread support for organized conversations
  - Reactions, pinned messages, and rich embeds
  - System messages for audit and automation
- **Identity & Security**:
  - User authentication with 2FA (TOTP) support
  - Multi-session management and device tracking
  - Hardened security with Argon2id password hashing and blind indexing
  - Zero-friction at-rest encryption for messages
  - Local QR code generation for privacy
- **Community Management**:
  - Flexible server and channel hierarchies
  - Role-based access control (RBAC) with granular permissions
  - Audit logging for server moderation
- **Media & Content**:
  - File attachments and media uploads with S3/MinIO support
  - User avatars and server icons
  - Auto-moderation with built-in content filtering
- **Voice & Video**:
  - High-performance WebRTC signaling via Mediasoup or Janus
  - Stage channels with speaker management
  - Screen sharing and video call support
- **Reliability & Maintenance**:
  - Self-test system for automated API validation
  - Integrated telemetry for performance monitoring
  - Flexible database support (SQLite/PostgreSQL) with automatic migrations
  - Redis integration for distributed caching

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
- Interactive docs at `http://localhost:8000/docs`
- Alternative docs at `http://localhost:8000/redoc`

## Configuration

Configuration is loaded from (in order):

1. `config/config.yaml`
2. `~/.plexichat/config/config.yaml`

### Key Configuration Options

```yaml
rate_limiting:
  enabled: true  # Enable/disable rate limiting middleware
  global:
    requests: 50
    window_seconds: 1.0
    burst: 10
  user:
    requests: 120
    window_seconds: 60.0
  ip:
    requests: 60
    window_seconds: 60.0

docs:
  enabled: true  # Enable/disable API documentation serving
  path: /docs/api
  title: PlexiChat API Documentation
```

See `gemini.md` for full configuration and deployment options.

## Project Structure

```
plexichat/
├── main.py              # Server entry point
├── requirements.txt     # Production dependencies
├── config/              # Default configuration templates
├── docs/                # API and system documentation
├── src/
│   ├── api/             # FastAPI application layer
│   │   ├── routes/      # REST API endpoints
│   │   ├── schemas/     # Pydantic data models
│   │   ├── middleware/  # Security, rate limiting, logging
│   │   └── websocket/   # Gateway event handlers
│   ├── core/            # Business logic (domain layer)
│   │   ├── auth/        # Identity and sessions
│   │   ├── messaging/   # Conversations and messages
│   │   ├── threads/     # Threaded conversations
│   │   ├── servers/     # Guild management and roles
│   │   ├── voice/       # WebRTC signaling and states
│   │   ├── automod/     # Content moderation engine
│   │   ├── notifications/ # Mention parsing and alerts
│   │   ├── events/      # Internal event dispatcher
│   │   ├── selftest/    # Self-test validation suite
│   │   └── ...          # Additional core modules
│   ├── tests/           # Comprehensive test suite
│   └── utils/           # Low-level utilities and submodules
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

### Quick Start

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests (recommended)
pytest -n auto -m "not slow"

# Or use make
make test
```

### Test Suite Overview

- **3000+ tests** across all repositories
- **85%+ coverage** target (enforced)
- **<30 minute** execution time
- **Zero security violations** enforced

### Documentation

- **Comprehensive Guide**: [docs/TESTING.md](docs/TESTING.md)
- **Test Suite Details**: [src/tests/README.md](src/tests/README.md)

## Version

Current version: `a.1.0-35` (Alpha)
