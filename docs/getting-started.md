# Getting Started

This guide will help you get started with the PlexiChat API.

## Prerequisites

- A PlexiChat account or bot application
- HTTP client (curl, Postman, or your preferred library)

## Authentication

### User Authentication

1. Register a new account:

```bash
curl -X POST https://api.example.com/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myusername",
    "email": "user@example.com",
    "password": "SecurePassword123!"
  }'
```

2. Login to get a session token:

```bash
curl -X POST https://api.example.com/api/v1/auth/login \
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
curl https://api.example.com/api/v1/users/@me \
  -H "Authorization: Bearer your_session_token_here"
```

### Bot Authentication

Bot tokens use the `Bot` prefix instead of `Bearer`:

```bash
curl https://api.example.com/api/v1/users/@me \
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
curl -X POST https://api.example.com/api/v1/auth/2fa \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_token": "challenge_token_here",
    "code": "123456"
  }'
```

## Version Negotiation

Before making API calls, check version compatibility:

```bash
curl -X POST https://api.example.com/api/v1/version/negotiate \
  -H "Content-Type: application/json" \
  -d '{
    "client_version": "a.1.0-1"
  }'
```

## Making Your First API Call

Get your user information:

```bash
curl https://api.example.com/api/v1/users/@me \
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

## Next Steps

- [REST API Reference](api/index.md) - Explore all endpoints
- [WebSocket Gateway](websocket/index.md) - Connect for real-time events
- [Rate Limits](rate-limits.md) - Understand rate limiting
