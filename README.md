# Plexichat Server

[![dev pipeline status](https://gitlab.plexichat.com/plexichat/plexichat/badges/dev/pipeline.svg)](https://gitlab.plexichat.com/plexichat/plexichat/-/pipelines)
[![master pipeline status](https://gitlab.plexichat.com/plexichat/plexichat/badges/master/pipeline.svg)](https://gitlab.plexichat.com/plexichat/plexichat/-/pipelines)
[![tag](https://img.shields.io/gitlab/v/tag/plexichat%2Fplexichat?gitlab_url=https://gitlab.plexichat.com)](https://gitlab.plexichat.com/plexichat/plexichat/-/tags)
[![issues](https://img.shields.io/gitlab/issues/open/plexichat%2Fplexichat?gitlab_url=https://gitlab.plexichat.com)](https://gitlab.plexichat.com/plexichat/plexichat/-/issues)
[![license](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue)](https://gitlab.plexichat.com/plexichat/plexichat/-/blob/master/LICENSE)

A real-time messaging platform server with REST API and WebSocket gateway.

## Installation

The recommended way to deploy Plexichat is via our standalone deployment scripts. These scripts automate version selection, network configuration, secret generation, and Docker Compose orchestration--without requiring a git clone.

**Linux / macOS:**
```bash
curl -sSL https://plexichat.com/deploy.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://plexichat.com/deploy.ps1 | iex
```

## Manual Installation (Development)

If you are a developer and wish to run the project from source:

```bash
git clone https://gitlab.plexichat.com/plexichat/plexichat.git
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
  - OAuth 2.0 support with PKCE and secure state verification
  - Multi-session management and device tracking
  - Hardened security with Argon2id password hashing and blind indexing
  - SSRF protection for external media proxying
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

## Quick Start (Development)

If you have cloned the repository and wish to run it manually (not recommended for production):

```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/Mac)
source .venv/bin/activate

# Install dependencies with hash verification
pip install --require-hashes -r requirements.txt

# Run server
python main.py

# Validate config without starting (pre-flight check)
python main.py pre-flight

# See all CLI options
python main.py --help
```

For full CLI reference, see [docs/cli/overview.md](docs/cli/overview.md).

### Dependency Management

Direct dependencies are listed in `requirements.in`. The `requirements.txt` file is auto-generated with pinned transitive dependencies and integrity hashes.

To update dependencies:

```bash
# Install uv (if not already installed)
pip install uv

# Edit requirements.in, then regenerate
uv pip compile requirements.in --generate-hashes -o requirements.txt

# Install with hash verification
pip install --require-hashes -r requirements.txt
```

Commit both `requirements.in` and `requirements.txt`.

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
  title: Plexichat API Documentation
```

See `docs/` and `config/` for repository-backed configuration details.

## Licensing

Plexichat requires a valid license for commercial use. Without one, the server runs in free tier mode (all base features work, premium features disabled).

**License File Location (in priority order):**
1. `PLEXICHAT_LICENSE` env var pointing to a file path
2. `PLEXICHAT_LICENSE` env var as a base64-encoded license JSON
3. `~/.plexichat/config/license` (default)
4. `~/.plexichat/config/license.json` (fallback)

License files are plain JSON with an Ed25519 signature. Example:
```json
{
  "version": "1.0",
  "instance_id": "my-instance",
  "issued_at": 1700000000,
  "features": { "bond": true, "join": true },
  "signature": "..."
}
```

**Hot-swapping:** The admin API (`POST /api/v1/admin/license/apply`) applies a license **in-memory only** — it does NOT persist to disk. On restart the original file is re-read. For permanent changes, update the license file on disk and call `POST /api/v1/admin/license/check` to reload it.

To obtain a license, contact sales@plexichat.com or visit https://plexichat.com.

## Project Structure

```
plexichat/
+-- main.py              # Server entry point
+-- requirements.in      # Direct dependency spec (source of truth)
+-- requirements.txt     # Pinned dependencies with integrity hashes (auto-generated)
+-- config/              # Default configuration templates
+-- docs/                # API and system documentation
+-- src/
|   +-- api/             # FastAPI application layer
|   |   +-- routes/      # REST API endpoints
|   |   +-- schemas/     # Pydantic data models
|   |   +-- middleware/  # Security, rate limiting, logging
|   |   +-- websocket/   # Gateway event handlers
|   +-- core/            # Business logic (domain layer)
|   |   +-- auth/        # Identity and sessions
|   |   +-- messaging/   # Conversations and messages
|   |   +-- threads/     # Threaded conversations
|   |   +-- servers/     # Guild management and roles
|   |   +-- voice/       # WebRTC signaling and states
|   |   +-- automod/     # Content moderation engine
|   |   +-- notifications/ # Mention parsing and alerts
|   |   +-- events/      # Internal event dispatcher
|   |   +-- selftest/    # Self-test validation suite
|   |   +-- ...          # Additional core modules
|   +-- tests/           # Comprehensive test suite
|   +-- utils/           # Low-level utilities and submodules
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

### Relationships

- `GET /api/v1/relationships/@me` - List friends, pending requests, and blocks for the current user
- `POST /api/v1/relationships` - Send a friend request
- `PUT /api/v1/relationships/{user_id}/accept` - Accept a pending incoming friend request
- `DELETE /api/v1/relationships/{user_id}` - Remove a friendship, cancel/decline a request, or unblock a user depending on current state
- `POST /api/v1/relationships/block` - Block a user and invalidate both users' relationship caches

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

Built-in admin panel for managing feedback, telemetry, admin account security, AutoMod, and closed-alpha access tokens.

- Open the UI at `http://localhost:8000/api/v1/admin/ui`
- The admin routes live under `/api/v1/admin/`
- Credentials are generated on first run
- 2FA is required by default (configurable via `admin_ui.require_otp`)
- Access is host-restricted to localhost by default

See root README.md for full configuration options.

## [!] Critical Backup Requirements

**Losing the encryption key files will make ALL encrypted data permanently unrecoverable.** The server will refuse to start if keyring decryption fails.

You **must** regularly back up these files from `~/.plexichat/data/`:

| File | What it protects |
|------|-----------------|
| `.machine_key` | Root KEK -- decrypts all keyrings. **Lose this = lose everything.** |
| `system_keyring.json` | Admin TOTP, API tokens, encrypted user fields |
| `file_keyring.json` | Media files at rest (avatars, attachments) |
| `message_keyring.json` | Message content (when `encrypt_messages: true`) |
| `plexichat.db` | All database content |

**Always stop the server before copying these files** to ensure consistent backups.
For production, use `PLEXICHAT_SYSTEM_KEY` env var instead of `.machine_key`.
See [SECURITY.md](SECURITY.md) for full details.

## Testing

### Quick Start

```bash
# Install dependencies with hash verification (includes test deps)
pip install --require-hashes -r requirements.txt

# Run the server test suite
pytest src/tests

# Optional: parallelize with pytest-xdist if you have it installed
# pip install pytest-xdist
# pytest -n auto src/tests
```

### Test Suite Overview

- Comprehensive pytest-based coverage under `src/tests/`
- Security, integration, performance, and property-based tests
- Coverage examples in `src/tests/README.md` use `--cov-fail-under=80`
- Optional parallel execution with `pytest-xdist`
- Relationship regression coverage lives in `src/tests/relationships/` and `src/tests/api/test_relationship_routes.py`, including cache invalidation and transaction-safety checks for send/accept/decline/cancel/block flows

### Documentation

- **Test Suite Details**: [src/tests/README.md](src/tests/README.md)
- **System Documentation**: [docs/index.md](docs/index.md)

## Version

Current version: `a.1.0-106` (Alpha)
