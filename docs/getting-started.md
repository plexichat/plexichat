# Getting Started

This guide will help you get started with the PlexiChat API.

## Prerequisites

- A PlexiChat account or bot application
- HTTP client (curl, Postman, or your preferred library)

## Server Setup

### Quick Start

```bash
cd plexichat
pip install -r requirements.txt
python main.py
```

### Optional: Video Processing

For video metadata extraction (duration, resolution, codec), install ffmpeg:

- **Linux**: `apt install ffmpeg` or `yum install ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Windows**: Download from https://ffmpeg.org/download.html and add to PATH

Without ffmpeg/ffprobe, video uploads still work but metadata won't be extracted.

The server will start on `http://localhost:8000` with:
- REST API at `/api/v1`
- WebSocket gateway at `/gateway`
- Swagger docs at `/docs`

### Configuration

See [Configuration Guide](configuration.md) for detailed setup options.

Data is stored in `~/.plexichat/` by default.

## Authentication

### User Authentication

1. Register a new account:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myusername",
    "email": "user@example.com",
    "password": "SecurePassword123!"
  }'
```

2. Login to get a session token:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myusername",
    "password": "SecurePassword123!"
  }'
```

Response:
```json
{
  "status": "success",
  "token": "your_session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "myusername"
  }
}
```

3. Use the token in subsequent requests:

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer your_session_token_here"
```

### Bot Authentication

Bot tokens use the `Bot` prefix instead of `Bearer`:

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bot your_bot_token_here"
```

## Two-Factor Authentication

If 2FA is enabled on the account, login returns a challenge:

```json
{
  "status": "two_factor_required",
  "challenge_token": "challenge_token_here",
  "methods": ["totp", "backup_code"],
  "expires_in": 300
}
```

Complete 2FA:

```bash
curl -X POST http://localhost:8000/api/v1/auth/2fa \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_token": "challenge_token_here",
    "code": "123456"
  }'
```

### Enabling 2FA

To enable 2FA on your account:

```bash
# Step 1: Request 2FA setup (returns QR code)
curl -X POST http://localhost:8000/api/v1/auth/2fa/enable \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{"password": "your_password"}'

# Response includes:
# - qr_code: Base64 encoded QR code image
# - secret: TOTP secret (for manual entry)
# - backup_codes: Array of backup codes

# Step 2: Confirm with TOTP code from authenticator app
curl -X POST http://localhost:8000/api/v1/auth/2fa/confirm \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{"code": "123456"}'
```

## Version Negotiation

Before making API calls, check version compatibility:

```bash
curl -X POST http://localhost:8000/api/v1/version/negotiate \
  -H "Content-Type: application/json" \
  -d '{
    "client_version": "a.1.0-1"
  }'
```

## Making Your First API Call

Get your user information:

```bash
curl http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer your_token_here"
```

Response:
```json
{
  "id": "123456789012345678",
  "username": "myusername",
  "email": "user@example.com",
  "avatar_url": null,
  "created_at": 1704067200,
  "email_verified": true,
  "totp_enabled": false
}
```

## Common Operations

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
  -d '{
    "content": "Hello, world!"
  }'
```

### Add a Reaction

```bash
curl -X PUT "http://localhost:8000/api/v1/channels/CHANNEL_ID/messages/MESSAGE_ID/reactions/%F0%9F%91%8D" \
  -H "Authorization: Bearer your_token"
```

Note: Emoji must be URL-encoded (👍 = %F0%9F%91%8D)

## Next Steps

- [Configuration](configuration.md) - Server configuration
- [REST API Reference](api/index.md) - Explore all endpoints
- [WebSocket Gateway](websocket/index.md) - Connect for real-time events
- [Rate Limits](rate-limits.md) - Understand rate limiting
