# Authentication API

Endpoints for user registration, login, and session management.

## POST /auth/register

Register a new user account.

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| username | string | Yes | 3-32 characters | Unique username |
| email | string | Yes | Valid email format | Email address |
| password | string | Yes | Min 8 characters | Password |

### Password Requirements

- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit
- At least one special character

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

| Status | Code | Description |
|--------|------|-------------|
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

| Status | Code | Description |
|--------|------|-------------|
| 401 | Invalid credentials | Wrong username/password |
| 403 | Account locked | Too many failed attempts |
| 403 | Email not verified | Email verification required |

## POST /auth/2fa

Complete two-factor authentication.

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| challenge_token | string | Yes | - | Token from login response |
| code | string | Yes | 6-8 characters | TOTP or backup code |

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

| Status | Code | Description |
|--------|------|-------------|
| 401 | Invalid code | Wrong 2FA code |
| 401 | Expired token | Challenge token expired |

## POST /auth/logout

Logout and revoke the current session.

### Headers

```
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

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "user_id": "123456789012345678",
    "ip_address": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "created_at": 1704067200,
    "last_used_at": 1704153600,
    "current": true
  }
]
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| id | string | Session's snowflake ID |
| user_id | string | User's ID |
| ip_address | string | IP address of session |
| user_agent | string | Browser/client user agent |
| created_at | int | Unix timestamp of creation |
| last_used_at | int | Unix timestamp of last use |
| current | bool | True if this is the current session |

## DELETE /auth/sessions/{session_id}

Revoke a specific session.

### Headers

```
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

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid session ID | ID format invalid |
| 404 | Session not found | Session doesn't exist |

## Session Management

### Token Types

| Type | Header Format | Description |
|------|---------------|-------------|
| User Session | `Bearer <token>` | User session token |
| Bot Token | `Bot <token>` | Bot application token |

### Session Limits

- Maximum concurrent sessions per user: 3
- Session expiration: 30 minutes (access token)
- Refresh token expiration: 7 days

### Account Lockout

- Maximum failed login attempts: 5
- Lockout duration: 15 minutes
