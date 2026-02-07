# Getting Started

Server setup, authentication, and first API calls.

## Prerequisites

- Python 3.11+
- pip package manager
- HTTP client (curl, Postman, or your preferred library)

## Server Setup

### Quick Start

```bash
cd plexichat
pip install -r requirements.txt
python main.py
```

The server starts on `http://localhost:8000` with:
- REST API at `/api/v1`
- WebSocket gateway at `/gateway`
- Interactive docs at `/docs` (Swagger UI)
- Alternative docs at `/redoc` (ReDoc)

### Optional: Video Processing

For video metadata extraction, install ffmpeg:

| Platform | Command |
|----------|---------|
| Linux (Debian/Ubuntu) | `apt install ffmpeg` |
| Linux (RHEL/CentOS) | `yum install ffmpeg` |
| macOS | `brew install ffmpeg` |
| Windows | Download from [ffmpeg.org](https://ffmpeg.org/download.html) |

Without ffmpeg, video uploads work but metadata won't be extracted.

### Configuration

See [Configuration Guide](configuration.md) for detailed setup options.

## Authentication

### Register a New Account

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myusername",
    "email": "user@example.com",
    "password": "SecurePassword123!"
  }'
```

**Response:**

```json
{
  "status": "success",
  "token": "your_session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "myusername",
    "email": "user@example.com",
    "avatar_url": null,
    "created_at": 1704067200,
    "email_verified": false,
    "totp_enabled": false
  }
}
```

### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myusername",
    "password": "SecurePassword123!"
  }'
```

### Using Your Token

Include the token in all authenticated requests:

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer your_session_token_here"
```

### Bot Authentication

Bot tokens use the `Bot` prefix:

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bot your_bot_token_here"
```

## Two-Factor Authentication

### When 2FA is Required

If 2FA is enabled, login returns a challenge:

```json
{
  "status": "two_factor_required",
  "token": null,
  "user": null,
  "challenge_token": "challenge_token_here",
  "methods": ["totp", "backup_code"],
  "expires_in": 300
}
```

### Complete 2FA

```bash
curl -X POST http://localhost:8000/api/v1/auth/2fa \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_token": "challenge_token_here",
    "code": "123456"
  }'
```

### Enable 2FA on Your Account

**Step 1:** Request 2FA setup

```bash
curl -X POST http://localhost:8000/api/v1/auth/2fa/enable \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{"password": "your_password"}'
```

Response includes:
- `secret` - TOTP secret for manual entry
- `qr_uri` - URI for QR code generation
- `backup_codes` - One-time recovery codes

**Step 2:** Confirm with authenticator code

```bash
curl -X POST http://localhost:8000/api/v1/auth/2fa/confirm \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{"code": "123456"}'
```

## Version Negotiation

Check client/server compatibility before making API calls:

```bash
curl -X POST http://localhost:8000/api/v1/version/negotiate \
  -H "Content-Type: application/json" \
  -d '{"client_version": "a.1.0-1"}'
```

## First API Calls

### Get Your Profile

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer your_token"
```

### Create a Server

```bash
curl -X POST http://localhost:8000/api/v1/servers \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Server",
    "description": "A cool server"
  }'
```

### Send a Message

```bash
curl -X POST http://localhost:8000/api/v1/channels/CHANNEL_ID/messages \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello, world!"}'
```

### Add a Reaction

```bash
curl -X PUT "http://localhost:8000/api/v1/channels/CHANNEL_ID/messages/MESSAGE_ID/reactions/%F0%9F%91%8D" \
  -H "Authorization: Bearer your_token"
```

Note: Emoji must be URL-encoded (thumbs up emoji = `%F0%9F%91%8D`)

### Update Your Presence

```bash
curl -X PUT http://localhost:8000/api/v1/users/@me/presence \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "online",
    "custom_status": "Working on PlexiChat"
  }'
```

## Password Requirements

Default password policy:

| Requirement | Value |
|-------------|-------|
| Minimum length | 12 characters |
| Uppercase letter | Required |
| Lowercase letter | Required |
| Digit | Required |
| Special character | Required |

Check current requirements:

```bash
curl http://localhost:8000/api/v1/auth/password-requirements
```

## Next Steps

- [Configuration](configuration.md) - Server configuration options
- [REST API Reference](api/index.md) - All available endpoints
- [WebSocket Gateway](websocket/index.md) - Real-time events
- [Rate Limits](rate-limits.md) - Rate limiting policies
