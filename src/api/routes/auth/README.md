# Auth routes

This directory defines the HTTP endpoints for authentication and session management.

## Overview

The `auth` package exposes registration, login, OAuth, two-factor authentication (2FA), session lifecycle, password reset, passkey (WebAuthn), and email verification. Each file defines a focused `APIRouter` that `router.py` aggregates into a single `router` tagged `Authentication`. The parent `routes` package mounts this router at `/auth`.

All handlers delegate to the core `AuthManager` (accessed through `src.api.get_auth()`) and translate domain exceptions into standardized `HTTPException` responses.

## File structure

| File | Description |
|------|-------------|
| `__init__.py` | Re-exports `router` from `router.py`. |
| `router.py` | Creates the top-level `APIRouter` and includes every sub-router. |
| `register.py` | `POST /register` — create an account and mint an initial session. |
| `login.py` | `POST /login` — authenticate with username and password. |
| `oauth.py` | `GET /oauth/{provider}/login` and `POST /oauth/{provider}/callback`. |
| `oauth_config.py` | Static `OAUTH_PROVIDERS` configuration for client IDs, secrets, and endpoints. |
| `two_factor.py` | `POST /2fa`, `GET /2fa/status`, `POST /2fa/enable`, `POST /2fa/confirm`, `POST /2fa/disable`. |
| `sessions.py` | `POST /logout`, `POST /ws-ticket`, `POST /refresh`, `GET /sessions`, `DELETE /sessions/{session_id}`, `POST /sessions/revoke-all`. |
| `password.py` | `GET /password-requirements`, `POST /password-reset/request`, `POST /password-reset/confirm`. |
| `passkeys.py` | WebAuthn endpoints: `POST /passkeys/options/register`, `POST /passkeys/register`, `POST /passkeys/options/authenticate`, `POST /passkeys/authenticate`, `GET /passkeys`, `DELETE /passkeys/{passkey_id}`, `PATCH /passkeys/{passkey_id}`. |
| `email_verification.py` | Email verification endpoints. |
| `cookies.py` | Helpers that set and clear the `HttpOnly` session cookie. |
| `helpers.py` | Shared helper `_user_to_response` that maps a domain user to `UserResponse`. |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/register` | Register a new user. |
| `POST` | `/auth/login` | Log in with credentials. Returns `two_factor_required` when 2FA is enabled. |
| `POST` | `/auth/2fa` | Complete a 2FA challenge. |
| `GET` | `/auth/2fa/status` | Get the 2FA status for the current user. |
| `POST` | `/auth/2fa/enable`, `/2fa/confirm`, `/2fa/disable` | Manage TOTP enrollment. |
| `GET` | `/auth/oauth/{provider}/login` | Start an OAuth flow. |
| `POST` | `/auth/oauth/{provider}/callback` | Complete an OAuth flow. |
| `POST` | `/auth/logout` | Revoke the current session and clear the session cookie. |
| `POST` | `/auth/ws-ticket` | Mint a single-use ticket for the WebSocket gateway. |
| `POST` | `/auth/refresh` | Rotate the session token and reissue the cookie. |
| `GET` | `/auth/sessions` | List active sessions. |
| `DELETE` | `/auth/sessions/{session_id}` | Revoke a single session. |
| `POST` | `/auth/sessions/revoke-all` | Revoke all sessions, optionally except the current session. |
| `GET` | `/auth/password-requirements` | Return password policy. |
| `POST` | `/auth/password-reset/request`, `/password-reset/confirm` | Request and confirm a password reset. |
| `POST` | `/auth/passkeys/options/register` | Get WebAuthn creation options. |
| `POST` | `/auth/passkeys/register` | Verify and store a new passkey. |
| `POST` | `/auth/passkeys/options/authenticate` | Get WebAuthn request options. |
| `POST` | `/auth/passkeys/authenticate` | Authenticate with a passkey. |
| `GET` | `/auth/passkeys` | List passkeys for the current user. |
| `DELETE` | `/auth/passkeys/{passkey_id}` | Revoke a passkey. |
| `PATCH` | `/auth/passkeys/{passkey_id}` | Rename a passkey. |

## Examples

### Register and log in

```python
# Register
POST /auth/register
Content-Type: application/json

{
  "username": "alice",
  "email": "alice@example.com",
  "password": "SecurePass123!"
}

# Log in — success response
{
  "status": "success",
  "token": "session_token_here",
  "user": { "id": "123", "username": "alice" }
}

# Log in — 2FA required response
{
  "status": "two_factor_required",
  "challenge_token": "challenge_token_here",
  "methods": ["totp", "backup_code"],
  "expires_in": 300
}
```

### Complete 2FA

```http
POST /auth/2fa
Content-Type: application/json

{
  "challenge_token": "challenge_token_here",
  "code": "123456"
}
```

Successful authentication also sets an `HttpOnly` session cookie. Use this cookie or the `Authorization: Bearer <token>` header for subsequent requests.

## Error handling

Handlers map domain exceptions to HTTP status codes:

| Status | Cause |
|--------|-------|
| `400` | `InvalidCredentialsError`, `WeakPasswordError`, `InvalidUsernameError`, `InvalidEmailError`, `TwoFactorInvalidError`, or request validation failure. |
| `401` | `TokenExpiredError`, `TokenInvalidError`, or missing authentication. |
| `403` | `AccountLockedError`, `EmailNotVerifiedError`, `PermissionDeniedError`, or CSRF validation failure on cookie-based refresh. |
| `404` | `UserNotFoundError` or `PasskeyNotFound`. |
| `409` | `UserExistsError` — username or email already taken. |
| `429` | Rate limit exceeded on passkey endpoints. |

Error responses use a consistent shape:

```json
{
  "detail": {
    "error": { "code": 400, "message": "Invalid credentials" }
  }
}
```

## Dependencies

* Core auth module (`AuthManager`) for account and session logic.
* Authentication middleware (`get_current_user`) for resolving the current user.
* Pydantic schemas in `src.api.schemas.auth` and `src.api.schemas.common` for validation.
* OAuth configuration in `oauth_config.py`.
* Feature module for applying new-user features after registration.

## Notes

* The router is tagged `Authentication` for OpenAPI grouping.
* Session tokens are delivered both as a JSON `token` field and as an `HttpOnly` cookie to protect against cross-site scripting (XSS).
* Cookie-based refresh requires the `X-Plexichat-Request: 1` header for cross-site request forgery (CSRF) protection.
* Passkey routes enforce rate limiting when `src.core.ratelimit` is available; if the import fails, the server logs a critical warning and optionally refuses to start in fail-closed mode.
