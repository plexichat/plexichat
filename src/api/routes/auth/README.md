# Auth Routes

## Purpose
Provides authentication and session endpoints for the API, including
registration, login, OAuth entrypoints, 2FA, and account recovery flows.

## File Layout

| File | Purpose |
|------|---------|
| `__init__.py` | Thin re-exporter: `from .router import router` |
| `router.py` | Creates the main `APIRouter`, includes all sub-routers |
| `oauth_config.py` | `OAUTH_PROVIDERS` static configuration dict |
| `register.py` | `POST /register` |
| `login.py` | `POST /login` |
| `oauth.py` | `GET /oauth/{provider}/login`, `POST /oauth/{provider}/callback` |
| `two_factor.py` | `POST /2fa`, `GET /2fa/status`, `POST /2fa/enable`, `POST /2fa/confirm`, `POST /2fa/disable` |
| `sessions.py` | `POST /logout`, `POST /refresh`, `GET /sessions`, `DELETE /sessions/{id}`, `POST /sessions/revoke-all` |
| `password.py` | `GET /password-requirements`, `POST /password-reset/request`, `POST /password-reset/confirm` |
| `passkeys.py` | All passkey (WebAuthn/FIDO2) endpoints |
| `helpers.py` | Shared helper `_user_to_response` |

## Main Entry Points
- POST /register
- POST /login
- POST /logout
- POST /oauth/{provider}/login
- POST /oauth/{provider}/callback
- POST /2fa
- GET /2fa/status
- POST /2fa/enable
- POST /2fa/confirm
- POST /2fa/disable
- POST /refresh
- GET /sessions
- DELETE /sessions/{session_id}
- POST /sessions/revoke-all
- GET /password-requirements
- POST /password-reset/request
- POST /password-reset/confirm
- POST /passkeys/options/register
- POST /passkeys/register
- POST /passkeys/options/authenticate
- POST /passkeys/authenticate
- GET /passkeys
- DELETE /passkeys/{passkey_id}
- PATCH /passkeys/{passkey_id}

## Usage

```python
# Login request
POST /auth/login
Content-Type: application/json

{
    "username": "alice",
    "password": "SecurePass123!",
    "device_info": {"fingerprint": "abc123", "type": "mobile"}
}

# Response (no 2FA):
{
    "status": "success",
    "token": "session_token_here",
    "user": { "id": 1, "username": "alice", ... }
}

# Response (2FA required):
{
    "status": "two_factor_required",
    "challenge_token": "challenge_token_here",
    "methods": ["totp", "backup_code"],
    "expires_in": 300
}

# Complete 2FA:
POST /auth/2fa
{
    "challenge_token": "challenge_token_here",
    "code": "123456"
}
```

## Error Handling

Routes translate domain exceptions from the auth manager into standard HTTP responses:

| HTTP Code | Raised For |
|-----------|-----------|
| 400 | `InvalidCredentialsError`, `WeakPasswordError`, `InvalidUsernameError`, `InvalidEmailError`, `TwoFactorInvalidError`, validation errors |
| 401 | `TokenExpiredError`, `TokenInvalidError` |
| 403 | `AccountLockedError`, `EmailNotVerifiedError`, `PermissionDeniedError` |
| 409 | `UserExistsError` (username/email already taken) |
| 404 | `UserNotFoundError` |

```python
# Typical error response:
HTTP 400
{
    "detail": "Invalid credentials",
    "code": "INVALID_CREDENTIALS"
}
```

## Dependencies
- Core auth module for account and session logic (`AuthManager`).
- API middleware for current-user resolution (`get_current_user` dependency).
- Auth schemas for request/response validation (Pydantic models).
- OAuth config (`oauth_config.py`) that maps provider names to client IDs, secrets, and endpoints.

## Notes
- Routes are grouped on an APIRouter tagged `Authentication`.
- The router in `router.py` includes all sub-routers; the parent `routes/__init__.py` imports `router` from `.auth` and mounts it at `/auth`.
- Error responses are standardized via common API schema types (HTTPException with Pydantic detail models).
- Passkeys use the WebAuthn/FIDO2 protocol with challenge-response flows.
