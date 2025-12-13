# Authentication API

Endpoints for user registration, login, session management, and two-factor authentication.

## POST /auth/register

Register a new user account.

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| username | string | Yes | 3-32 characters | Unique username |
| email | string | Yes | Valid email format | Email address |
| password | string | Yes | See requirements | Password |

### Password Requirements

Default requirements (configurable):
- Minimum 12 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit
- At least one special character

Check current requirements via `GET /auth/password-requirements`.

### Example Request

```json
{
  "username": "johndoe",
  "email": "john@example.com",
  "password": "SecurePass123!"
}
```

### Response (201 Created)

```json
{
  "status": "success",
  "token": "session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "johndoe",
    "email": "john@example.com",
    "avatar_url": null,
    "created_at": 1704067200,
    "email_verified": false,
    "totp_enabled": false
  }
}
```

### Error Responses

| Status | Message | Description |
|--------|---------|-------------|
| 400 | Invalid input | Validation failed |
| 400 | Weak password | Password doesn't meet requirements |
| 409 | Already exists | Username or email taken |

## POST /auth/login

Authenticate a user and obtain a session token.

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| username | string | Yes | Username or email |
| password | string | Yes | Password |

### Example Request

```json
{
  "username": "johndoe",
  "password": "SecurePass123!"
}
```

### Response (200 OK) - Success

```json
{
  "status": "success",
  "token": "session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "johndoe",
    "email": "john@example.com",
    "avatar_url": null,
    "created_at": 1704067200,
    "email_verified": true,
    "totp_enabled": false
  }
}
```

### Response (200 OK) - 2FA Required

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

### Error Responses

| Status | Message | Description |
|--------|---------|-------------|
| 401 | Invalid credentials | Wrong username/password |
| 403 | Account locked | Too many failed attempts |
| 403 | Email not verified | Email verification required |

## POST /auth/2fa

Complete two-factor authentication challenge.

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| challenge_token | string | Yes | Token from login response |
| code | string | Yes | 6-digit TOTP or backup code |

### Example Request

```json
{
  "challenge_token": "challenge_token_from_login",
  "code": "123456"
}
```

### Response (200 OK)

```json
{
  "status": "success",
  "token": "session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "johndoe"
  }
}
```

### Error Responses

| Status | Message | Description |
|--------|---------|-------------|
| 401 | Invalid code | Wrong 2FA code |
| 401 | Expired token | Challenge token expired |

## POST /auth/logout

Logout and revoke the current session.

### Headers

```http
Authorization: Bearer <session_token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

## GET /auth/sessions

Get all active sessions for the current user.

### Headers

```http
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "ip_address": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "created_at": 1704067200,
    "last_activity": 1704153600,
    "current": true
  }
]
```

## DELETE /auth/sessions/{session_id}

Revoke a specific session.

### Headers

```http
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| session_id | string | Session's snowflake ID |

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Message | Description |
|--------|---------|-------------|
| 400 | Invalid session ID | ID format invalid |
| 404 | Session not found | Session doesn't exist |

## POST /auth/sessions/revoke-all

Revoke all sessions except optionally the current one.

### Headers

```http
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| except_current | bool | true | Keep current session active |

### Response (200 OK)

```json
{
  "success": true,
  "revoked_count": 3
}
```

## GET /auth/2fa/status

Get current 2FA status for the authenticated user.

### Headers

```http
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "enabled": false,
  "backup_codes_remaining": 0
}
```

## POST /auth/2fa/enable

Start 2FA setup process. Returns QR code and secret.

### Headers

```http
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| password | string | Yes | Current password for verification |

### Response (200 OK)

```json
{
  "secret": "JBSWY3DPEHPK3PXP",
  "qr_uri": "otpauth://totp/PlexiChat:johndoe?secret=...",
  "backup_codes": ["12345678", "23456789", "..."]
}
```

### Error Responses

| Status | Message | Description |
|--------|---------|-------------|
| 400 | Password required | Missing password |
| 401 | Invalid password | Wrong password |
| 409 | 2FA is already enabled | Already active |

## POST /auth/2fa/confirm

Confirm 2FA setup with TOTP code from authenticator app.

### Headers

```http
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | Yes | 6-digit code from authenticator |

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Message | Description |
|--------|---------|-------------|
| 400 | Valid 6-digit code required | Invalid code format |
| 400 | 2FA setup not started | No pending setup |
| 401 | Invalid code | Wrong code |

## POST /auth/2fa/disable

Disable 2FA on the account.

### Headers

```http
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| password | string | Yes | Current password |
| code | string | Yes | Current 2FA code |

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Message | Description |
|--------|---------|-------------|
| 400 | Password required | Missing password |
| 400 | 2FA code required | Missing code |
| 400 | 2FA is not enabled | Not active |
| 401 | Invalid password or code | Wrong credentials |

## GET /auth/password-requirements

Get server password policy (no authentication required).

### Response (200 OK)

```json
{
  "min_length": 12,
  "max_length": 128,
  "require_uppercase": true,
  "require_lowercase": true,
  "require_digit": true,
  "require_special": true
}
```

## Token Types

| Type | Header Format | Description |
|------|---------------|-------------|
| User Session | `Bearer <token>` | User session token |
| Bot Token | `Bot <token>` | Bot application token |

## Session Limits

| Setting | Default |
|---------|---------|
| Max concurrent sessions | 3 |
| Access token expiration | 30 minutes |
| Refresh token expiration | 7 days |

## Account Lockout

| Setting | Default |
|---------|---------|
| Max failed attempts | 5 |
| Lockout duration | 15 minutes |
