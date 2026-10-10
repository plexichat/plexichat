# Admin authentication

The admin authentication module provides a complete authentication system for the Plexichat admin panel. It supports password-based login with Argon2id hashing, TOTP-based two-factor authentication, session management, and password policy enforcement.

The module uses a **mixin-based architecture** where individual concerns (authentication, OTP, sessions, passwords, security) are isolated into composable classes. The `AdminAuth` composer class combines all mixins into a single interface.

## Architecture

```
AdminAuth (composer)
├── AuthenticationMixin  — login, user provisioning
├── OTPMixin             — OTP verification (setup + login)
├── OTPSetupMixin        — OTP lifecycle (begin, disable, backup codes)
├── SessionMixin         — session creation and validation
├── PasswordMixin        — password policy and rotation
├── SecurityMixin        — security posture reporting
└── AdminAuthBase        — shared database session
```

A lazy-initialized singleton is exposed via the `__init__.py` package, allowing module-level function calls that delegate to the `AdminAuth` instance.

## Key files

| File | Purpose |
|------|---------|
| `__init__.py` | Package entry point; exposes singleton wrappers and `init_auth()` |
| `base.py` | `AdminAuthBase` — holds the database session reference |
| `composer.py` | `AdminAuth` — composes all mixins via MRO |
| `dataclasses.py` | `AdminLoginResult` and `AdminSecurityStatus` return types |
| `auth_mixin.py` | `AuthenticationMixin` — admin login and user provisioning |
| `otp_mixin.py` | `OTPMixin` — TOTP code verification during login |
| `otp_setup_mixin.py` | `OTPSetupMixin` — begin, disable, and regenerate backup codes |
| `password_mixin.py` | `PasswordMixin` — policy validation and password rotation |
| `session_mixin.py` | `SessionMixin` — bearer token session lifecycle |
| `security_mixin.py` | `SecurityMixin` — account security status reporting |
| `helpers.py` | Private utilities: token hashing, OTP challenges, rate limiting |

## Usage

Initialize the auth module with a database session before calling any function:

```python
from src.core.admin.auth import init_auth, authenticate_admin

init_auth(db)
result = authenticate_admin(db, "admin", "password", ip="127.0.0.1")
if result.success and not result.requires_otp_verify:
    print(f"Session token: {result.token}")
```

To verify an OTP code after login:

```python
from src.core.admin.auth import verify_otp

result = verify_otp(db, admin_id, code="123456", challenge_token=token)
```

To manage sessions:

```python
from src.core.admin.auth import create_session, validate_session, logout

token = create_session(db, admin_id, expires_hours=8)
admin_id = validate_session(db, token)
logout(db, token)
```

## Configuration

The module reads configuration from `config.get("admin_ui", {})`:

- `require_otp` (bool, default `True`) — whether OTP is mandatory
- `force_password_change_first_login` (bool, default `True`) — force password rotation on first login
- `rate_limit.max_attempts` (int, default `5`) — max login attempts before lockout
- `rate_limit.window_seconds` (int, default `300`) — rate limit window
- `rate_limit.lockout_seconds` (int, default `900`) — lockout duration
- `security.password_policy` — password complexity requirements

## Security considerations

- Passwords are hashed with Argon2id via `src.utils.encryption` (`src/core/admin/auth/auth_mixin.py:37`)
- OTP secrets are encrypted at rest using `encrypt_data` with a per-admin context (`src/core/admin/auth/otp_mixin.py:61`)
- Challenge tokens are single-use and stored in Valkey with a 300-second TTL (`src/core/admin/auth/helpers.py:29`)
- Rate limiting is enforced atomically via the database cache layer (`src/core/admin/auth/helpers.py:214`)
- Password rotation invalidates all existing sessions (`src/core/admin/auth/password_mixin.py:136`)
- Admin credentials are saved to `~/.plexichat/admin_credentials.txt` with `0o600` permissions (`src/core/admin/auth/helpers.py:132`)

## Related code

- `src/utils/encryption.py` — Argon2id hashing, encryption/decryption utilities
- `src/core/database/__init__.py` — `check_rate_limit`, `cache_set`, `cache_get`
- `src/core/admin/permissions.py` — `log_admin_action` for audit trails
- `src/core/auth/totp.py` — `hash_backup_codes` for backup code management
