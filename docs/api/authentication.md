# Authentication

This guide covers registration, login, session management, two-factor authentication, OAuth, and passkeys.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this guide are prefixed with `/api/v1/auth` unless noted. The API prefix `api.api_prefix` defaults to `/api/v1` (`src/config_defaults.py:131`).

## Register a new account

### POST /api/v1/auth/register

Register a new user account.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | Yes | 3–32 characters, unique |
| `email` | string | Yes | Valid email format |
| `password` | string | Yes | Must meet password policy |
| `age` | integer | No | Required if age gate is enabled in boolean mode; must be `>0` |
| `dob` | string | No | Required if age gate is enabled in date-of-birth mode; format `YYYY-MM-DD` |

Password requirements are configurable. Defaults: minimum 12 characters, at least one uppercase letter, one lowercase letter, one digit, and one special character. Query `GET /api/v1/auth/password-requirements` for the active policy.

**Example request**

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "johndoe",
    "email": "john@example.com",
    "password": "SecurePass123!"
  }'
```

**Response `201 Created`**

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
    "totp_enabled": false,
    "age_verified": false,
    "badges": []
  }
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Invalid input or weak password |
| `409` | Username or email already exists |

## Log in

### POST /api/v1/auth/login

Authenticate a user and obtain a session token.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | Yes | Username or email |
| `password` | string | Yes | Password |
| `device_fingerprint` | string | No | Stable client device fingerprint (8–256 chars). Required for trusted-device (2FA skip) support; devices can be marked trusted via `POST /api/v1/auth/devices/trust` |

**Example request**

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "johndoe",
    "password": "SecurePass123!"
  }'
```

**Response `200 OK` — success**

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
    "totp_enabled": false,
    "age_verified": true,
    "badges": []
  }
}
```

**Response `200 OK` — 2FA required**

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

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Invalid credentials |
| `403` | Account locked or email not verified |

> **Trusted devices:** when 2FA is enabled and the request carries the fingerprint of a device the user previously marked trusted, the server skips the 2FA challenge and returns a session directly. A missing fingerprint is never trusted.
>
> **Session duration:** sessions created on a trusted device may live longer than the default. Configure via the `sessions` block:
>
> | Key | Default | Meaning |
> |---|---|---|
> | `expire_hours` | `168` | Session TTL for untrusted/unknown devices |
> | `trusted_expire_hours` | `720` | Session TTL for trusted devices; set to `null` to disable and always use `expire_hours` |
> | `extend_on_activity` | `true` | Re-arm session expiry on activity |
> | `extend_threshold_hours` | `24` | Re-arm threshold for untrusted sessions |
> | `trusted_extend_threshold_hours` | `168` | Re-arm threshold for trusted sessions (falls back to `trusted_expire_hours`, then `extend_threshold_hours`) |

## Manage devices

### GET /api/v1/auth/devices

List the signed-in user's known devices with trusted status.

**Response `200 OK`**

```json
[
  {
    "id": "123456789012345678",
    "name": "Laptop de casa",
    "device_type": "desktop",
    "trusted": true,
    "first_seen_at": 1704067200,
    "last_seen_at": 1735689600
  }
]
```

### PATCH /api/v1/auth/devices/{device_id}

Rename a device.

**Request body** `{ "name": "Laptop de casa" }` — `404` if the device does not belong to the user.

### DELETE /api/v1/auth/devices/{device_id}

Forget a device and revoke all sessions created from it. `404` if not found.

### POST /api/v1/auth/devices/trust

Mark a device as trusted (skips the 2FA challenge at login). Only devices the account has previously authenticated from can be trusted.

**Request body** `{ "fingerprint": "a1b2c3d4e5f6..." }`

| Status | Meaning |
|---|---|
| `200` | Device marked trusted |
| `404` | Fingerprint does not match any known device |

### POST /api/v1/auth/devices/untrust

Remove trusted status from a device. Same body and errors as `/trust`.

## Complete two-factor authentication

### POST /api/v1/auth/2fa

Complete a two-factor challenge.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `challenge_token` | string | Yes | Token from the login response |
| `code` | string | Yes | 6-digit TOTP or backup code |

**Example request**

```json
{
  "challenge_token": "challenge_token_from_login",
  "code": "123456"
}
```

**Response `200 OK`**

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

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Invalid code or expired challenge token |

## Log out

### POST /api/v1/auth/logout

Revoke the current session.

```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

**Response `200 OK`**

```json
{
  "success": true
}
```

## Refresh a session

### POST /api/v1/auth/refresh

Refresh the current session token. The server extends the lifetime when the session is near expiration.

```bash
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

Returns a standard `LoginResponse`.

## Reset a password

### POST /api/v1/auth/password-reset/request

Request a password reset. The endpoint always returns `200` to prevent email enumeration.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Account email address |

**Response `200 OK`**

```json
{
  "success": true
}
```

### POST /api/v1/auth/password-reset/confirm

Complete a password reset using the token from the reset email.

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | Yes | Reset token from email |
| `new_password` | string | Yes | New password |

**Response `200 OK`**

```json
{
  "success": true
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Token malformed or password does not meet requirements |
| `401` | Reset token unknown or expired |

## Manage sessions

### GET /api/v1/auth/sessions

Get all active sessions for the current user.

```bash
curl -X GET http://localhost:8000/api/v1/auth/sessions \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

**Response `200 OK`**

```json
[
  {
    "id": "123456789012345678",
    "device_id": null,
    "ip_address": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "created_at": 1704067200,
    "expires_at": 1704672000,
    "last_activity": 1704153600,
    "current": true
  }
]
```

### DELETE /api/v1/auth/sessions/{session_id}

Revoke a specific session.

**Headers**

```http
Authorization: Bearer <token>
```

**Path parameters**

| Field | Type | Description |
|---|---|---|
| `session_id` | integer | Snowflake ID from the sessions list |

**Response `200 OK`**

```json
{
  "success": true
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Invalid session ID format |
| `401` | Not authenticated |
| `404` | Session not found or belongs to another user |

### POST /api/v1/auth/sessions/revoke-all

Revoke all sessions for the current user, optionally keeping the current one. If the current session is revoked, the session cookie is cleared.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

| Field | Type | Default | Description |
|---|---|---|---|
| `except_current` | boolean | `true` | Keep the current session |

**Response `200 OK`**

```json
{
  "success": true,
  "revoked_count": 3
}
```

## Mint a WebSocket ticket

### POST /api/v1/auth/ws-ticket

Mint a short-lived, single-use ticket for the WebSocket gateway handshake. Request a ticket immediately before connecting and send it as the gateway `token` in `IDENTIFY`. The ticket is consumed on first use and expires quickly (`expires_in`). Only available for interactive (non-bot) sessions; returns `409` otherwise.

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

```json
{
  "ticket": "wst.example_ticket_value",
  "expires_in": 60
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `401` | Not authenticated |
| `409` | No underlying interactive session |

The WebSocket gateway is at `/gateway` (`src/api/websocket/gateway.py:261`).

## Manage two-factor authentication

### GET /api/v1/auth/2fa/status

Get current 2FA status for the authenticated user.

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

```json
{
  "enabled": false,
  "backup_codes_remaining": 0
}
```

### POST /api/v1/auth/2fa/enable

Start the 2FA setup flow. Returns a TOTP secret, QR URI, and backup codes.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `password` | string | Yes | Current password |

**Response `200 OK`**

```json
{
  "secret": "JBSWY3DPEHPK3PXP",
  "qr_uri": "otpauth://totp/Plexichat:johndoe?secret=...",
  "backup_codes": ["12345678", "23456789", "..."]
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Password required |
| `401` | Invalid password |
| `409` | 2FA already enabled |

### POST /api/v1/auth/2fa/confirm

Confirm 2FA setup with a code from your authenticator app.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | Yes | 6-digit code |

**Response `200 OK`**

```json
{
  "success": true
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Invalid code format or setup not started |
| `401` | Invalid code |

### POST /api/v1/auth/2fa/disable

Disable 2FA.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `password` | string | Yes | Current password |
| `code` | string | Yes | Current 2FA code |

**Response `200 OK`**

```json
{
  "success": true
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `400` | Missing password, missing code, or 2FA not enabled |
| `401` | Invalid password or code |

## Get password requirements

### GET /api/v1/auth/password-requirements

Get the server password policy. No authentication required.

**Response `200 OK`**

```json
{
  "min_length": 12,
  "max_length": 128,
  "require_uppercase": true,
  "require_lowercase": true,
  "require_digit": true,
  "require_special": true,
  "age_gate_enabled": false,
  "age_verification_type": "boolean",
  "minimum_age": 13
}
```

## Token types

| Type | Header | Description |
|---|---|---|
| User session | `Bearer <token>` | User session token |
| Bot token | `Bot <token>` | Bot application token |

## Session limits and lockout

| Setting | Value |
|---|---|
| Max sessions per user | 10 |
| Session expiration | 7 days (168 hours) |
| Extend on activity | Yes |
| Max failed login attempts | 5 |
| Lockout duration | 15 minutes |

## Sign in with OAuth

OAuth lets users sign in with Google, GitHub, Microsoft, or GitLab. PKCE is enabled by default and uses `S256`.

### GET /api/v1/auth/oauth/{provider}/login

Initiate an OAuth login flow.

**Path parameters**

| Field | Type | Description |
|---|---|---|
| `provider` | string | `google`, `github`, `microsoft`, or `gitlab` |

**Query parameters**

| Field | Type | Required | Description |
|---|---|---|---|
| `redirect_uri` | string | Yes | Must exactly match an entry in the provider's `allowed_redirect_uris` allow-list. Only a trailing slash is normalized; prefix or substring matches are rejected with `400` (`src/api/routes/auth/oauth.py:51`) |
| `code_challenge` | string | Yes when PKCE is enabled (default) | S256 challenge generated by the browser (43–128 unreserved characters `A-Z a-z 0-9 - . _ ~`). Only `code_challenge_method=S256` is accepted. The server never mints or returns a verifier (`src/api/routes/auth/oauth.py:122`) |
| `code_challenge_method` | string | No | Must be `S256` if provided |

**Response `200 OK`**

```json
{
  "url": "https://accounts.google.com/o/oauth2/v2/auth?...",
  "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
  "state": "random_state_string"
}
```

The server also sets a short-lived `HttpOnly` cookie named `plexichat_oauth_state` scoped to `${api.api_prefix}/auth/oauth` (`src/api/routes/auth/oauth.py:91`). You must send the same `state` value in the callback, and the cookie must match it; a mismatch fails the request and blocks login CSRF.

### POST /api/v1/auth/oauth/{provider}/callback

Handle the callback from the OAuth provider.

**Path parameters**

| Field | Type | Description |
|---|---|---|
| `provider` | string | `google`, `github`, `microsoft`, or `gitlab` |

**Request body**

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | Yes | Authorization code from the provider |
| `state` | string | Yes | State from the provider; must match the `plexichat_oauth_state` cookie |
| `redirect_uri` | string | Yes | Same URI used in initiation (re-checked against the allow-list) |
| `code_verifier` | string | Yes when PKCE is enabled | Browser-held PKCE verifier; the server verifies it against the stored `code_challenge` with `verify_pkce` (`src/api/routes/auth/oauth.py:413`, `src/api/schemas/auth.py:210`) |

**Response `200 OK`**

Standard `LoginResponse` (same as `POST /api/v1/auth/login`).

**OAuth error responses**

| Status | Meaning |
|---|---|
| `400` | `redirect_uri` not on allow-list, invalid `code_challenge` format, unsupported `code_challenge_method`, or state cookie mismatch |
| `404` | Unsupported provider |
| `429` | Too many pending OAuth requests |
| `401` | Token exchange or user-info fetch failed |

## Authenticate with passkeys (WebAuthn/FIDO2)

Plexichat supports WebAuthn/FIDO2 passkeys for passwordless authentication.

**Security features**

- Platform authenticators: Touch ID, Face ID, Windows Hello
- Cross-platform authenticators: YubiKey, Titan Security Key
- Discoverable credentials for usernameless authentication
- Signature counters for clone detection
- Backup detection for synced credentials (Apple Keychain, Google Password Manager)

### POST /api/v1/auth/passkeys/options/register

Get WebAuthn registration options for the current user.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

```json
{
  "device_name": "My YubiKey"
}
```

**Response `200 OK`**

```json
{
  "challenge_id": "challenge_uuid",
  "options": {
    "rp": {
      "name": "Plexichat",
      "id": "chat.example.com"
    },
    "user": {
      "id": "base64_user_handle",
      "name": "username",
      "displayName": "User Name"
    },
    "challenge": "base64_challenge",
    "pubKeyCredParams": [...],
    "excludeCredentials": [...],
    "authenticatorSelection": {
      "residentKey": "preferred",
      "userVerification": "preferred"
    }
  }
}
```

### POST /api/v1/auth/passkeys/register

Complete passkey registration with the credential from the authenticator.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

```json
{
  "challenge_id": "challenge_uuid",
  "credential": {
    "id": "credential_id",
    "rawId": "base64_raw_id",
    "type": "public-key",
    "response": {
      "clientDataJSON": "base64_json",
      "attestationObject": "base64_object"
    }
  }
}
```

**Response `200 OK`**

```json
{
  "id": "123456789012345678",
  "credential_id": "base64url_credential_id",
  "device_name": "My YubiKey",
  "device_type": "cross-platform",
  "created_at": 1704067200,
  "last_used_at": null,
  "backed_up": false,
  "revoked": false
}
```

### POST /api/v1/auth/passkeys/options/authenticate

Get authentication options for passkey login. Call this before showing the browser passkey prompt.

**Request body**

```json
{
  "username": "johndoe"
}
```

**Response `200 OK`**

```json
{
  "challenge_id": "challenge_uuid",
  "options": {
    "challenge": "base64_challenge",
    "rpId": "chat.example.com",
    "allowCredentials": [...],
    "userVerification": "preferred"
  }
}
```

### POST /api/v1/auth/passkeys/authenticate

Complete passkey authentication.

**Request body**

```json
{
  "challenge_id": "challenge_uuid",
  "credential": {
    "id": "credential_id",
    "rawId": "base64_raw_id",
    "type": "public-key",
    "response": {
      "clientDataJSON": "base64_json",
      "authenticatorData": "base64_data",
      "signature": "base64_sig",
      "userHandle": "base64_user_handle"
    }
  }
}
```

**Response `200 OK`**

Standard `LoginResponse`.

### GET /api/v1/auth/passkeys

List registered passkeys for the current user.

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

```json
[
  {
    "id": "123456789012345678",
    "credential_id": "base64url_credential_id",
    "device_name": "My YubiKey",
    "device_type": "cross-platform",
    "created_at": 1704067200,
    "last_used_at": 1704153600,
    "backed_up": false,
    "revoked": false
  }
]
```

### DELETE /api/v1/auth/passkeys/{passkey_id}

Revoke a passkey.

**Headers**

```http
Authorization: Bearer <token>
```

**Response `200 OK`**

```json
{
  "success": true
}
```

### PATCH /api/v1/auth/passkeys/{passkey_id}

Rename a passkey.

**Headers**

```http
Authorization: Bearer <token>
```

**Request body**

```json
{
  "name": "New Device Name"
}
```

**Response `200 OK`**

```json
{
  "success": true
}
```

## Authentication method priority

When multiple methods are enabled, the login flow uses this priority:

1. Passkey, if the user has registered passkeys.
2. Password and 2FA.
3. OAuth with an external provider.

Users can disable password authentication if at least one passkey is registered.
