# managers/ — AuthManager Mixin Layout

## File Layout

| File              | Mixin / Class       | Responsibility |
|-------------------|---------------------|----------------|
| `base.py`         | `AuthManager`       | Composition class + shared helpers (`__init__`, `_log_audit`, `_track_ip`, `_track_device`, `_encrypt_ua`, `_ua_index`, serialization, config access, `_ensure_system_user`, `_get_timestamp`) |
| `registration.py` | `RegistrationMixin` | `register`, `verify_email`, `resend_verification`, `_send_verification_email` |
| `session.py`      | `SessionMixin`      | `login`, `create_session_for_user`, `verify_token`, `_verify_session_token`, `_verify_bot_token`, `refresh_session`, `logout`, `logout_all`, `logout_all_users`, `get_sessions`, `revoke_session`, `_create_session`, `_handle_failed_login` |
| `twofactor.py`    | `TwoFactorMixin`    | `_create_2fa_challenge`, `complete_2fa`, `setup_2fa`, `confirm_2fa`, `disable_2fa`, `regenerate_backup_codes`, `get_2fa_status` |
| `password.py`     | `PasswordMixin`     | `change_password`, `request_password_reset`, `reset_password`, `validate_password` |
| `bot.py`          | `BotMixin`          | `create_bot`, `get_bot`, `get_user_bots`, `regenerate_bot_token`, `update_bot_permissions`, `disable_bot`, `enable_bot`, `delete_bot` |
| `api_token.py`    | `ApiTokenMixin`     | All `api_access_token` methods + `verify_api_access_token`, event recording, scopes |
| `profile.py`      | `ProfileMixin`      | `update_user`, `get_user`, `get_user_by_username`, `get_user_profiles_bulk`, `get_users_bulk`, `grant_permission`, `_get_user_data_cached`, `_dict_to_user` |
| `device.py`       | `DeviceMixin`       | `get_devices`, `rename_device`, `revoke_device` |
| `ip_blacklist.py` | `IpBlacklistMixin`  | `block_ip`, `unblock_ip`, `is_ip_blocked`, `get_blocked_ips` |
| `audit.py`        | `AuditMixin`        | `get_login_history`, `get_security_events` |
| `oauth.py`        | `OAuthMixin`        | `has_capability`, `require_capability`, `oauth_login` |
| `deletion.py`     | `DeletionMixin`     | `schedule_account_deletion`, `cancel_account_deletion`, `delay_account_deletion`, `force_purge_account` |
| `passkey.py`      | `PasskeyMixin`      | All passkey methods (thin delegation to `.passkeys` manager) |

## Public API

The public API is unchanged:

```python
from src.core.auth.manager import AuthManager  # still works
from src.core import auth                      # still works
```

## Usage

```python
from src.core.auth.manager import AuthManager

auth_mgr = AuthManager(db, email_sender=smtp_sender)

# Registration
result = auth_mgr.register(username="alice", email="alice@example.com", password="SecurePass123!")
# Returns AuthResult with status=AuthStatus.SUCCESS and user/user token

# Login
result = auth_mgr.login(username="alice", password="SecurePass123!",
                        device_info={"fingerprint": "abc123", "type": "mobile"},
                        ip_address="192.168.1.1")
# Returns AuthResult — if 2FA enabled, status=TWO_FACTOR_REQUIRED with challenge_token

# Token verification
info = auth_mgr.verify_token("session_token_here", ip_address="192.168.1.1")
# Returns TokenInfo with account_id, permissions, rate_limit_tier, etc.

# Session management
sessions = auth_mgr.get_sessions(user_id=42)
auth_mgr.revoke_session(user_id=42, session_id=session.id)

# Password reset
auth_mgr.request_password_reset(email="alice@example.com")
auth_mgr.reset_password(user_id=42, reset_token="token", new_password="NewSecurePass456!")
```

## Error Handling

All auth operations raise domain-specific exceptions from `src.core.auth.exceptions`:

- `InvalidCredentialsError` — Wrong username/email or password (login, registration with existing username).
- `AccountLockedError` — Account locked due to failed attempts or scheduled deletion.
- `EmailNotVerifiedError` — Login attempted when email verification is required but not completed.
- `TokenExpiredError` — Session or bot token has expired.
- `TokenInvalidError` — Token is malformed, revoked, or fails binding checks (IP/User-Agent mismatch).
- `TwoFactorInvalidError` — Invalid 2FA code or backup code provided.
- `UserExistsError` — Registration with an already-taken username or email.
- `UserNotFoundError` — User lookup by ID or username returned no results.
- `WeakPasswordError` — Password does not meet strength requirements.
- `InvalidUsernameError` — Username violates formatting rules.
- `InvalidEmailError` — Email fails validation.
- `PermissionDeniedError` — User lacks required capability for an OAuth operation.
- `BotLimitExceededError` — User has reached the maximum number of bots.

```python
try:
    result = auth_mgr.login(username, password)
except InvalidCredentialsError:
    print("Invalid username or password")
except AccountLockedError as e:
    print(f"Account locked until: {e.locked_until}")
except EmailNotVerifiedError:
    print("Please verify your email first")
```

Token verification may also raise `TokenInvalidError("Token verification rate limit exceeded")` when the rate-limit threshold is hit.

## Composition Order (MRO)

The `AuthManager` class in `base.py` inherits from `BaseManager` then all mixins:

```
AuthManager -> BaseManager -> RegistrationMixin -> SessionMixin ->
TwoFactorMixin -> PasswordMixin -> BotMixin -> ApiTokenMixin ->
ProfileMixin -> DeviceMixin -> IpBlacklistMixin -> AuditMixin ->
OAuthMixin -> DeletionMixin -> PasskeyMixin
```

Shared helpers (`_log_audit`, `_track_ip`, `_track_device`, etc.) are defined
directly on `AuthManager` in `base.py`, so they take priority in MRO over any
mixin definitions.

## Dependencies

- `src.core.base.BaseManager` — Provides `_db`, `_generate_id()`, `_user_exists()`.
- `src.utils.encryption.EncryptionManager` — Password hashing/verification, data encryption, blind indexing.
- `src.core.auth.models` — `User`, `Session`, `TokenInfo`, `AuthResult`, `AuthStatus`, `AccountType`, `AuditEventType`.
- `src.core.auth.exceptions` — All domain-specific exception classes listed above.
- `src.core.auth.blacklist` — `BlacklistManager` for IP-based blocking.
- `src.core.auth.deletion_log` — Account deletion event tracking.
- `src.core.auth.passkeys` — `PasskeyManager` for WebAuthn/FIDO2 support.
- `src.core.auth.tokens` — Session/bot token creation, parsing, and hash verification.
- `src.core.auth.permissions` — JSON-based permission parsing.
- `src.core.database` — `cached` decorator and `invalidate_pattern` for cache management.

## Design Rules

- Each mixin accesses `self._db`, `self._config`, `self.crypto`, etc. from the
  base class — these are set in `AuthManager.__init__` before mixin methods run.
- Mixin methods freely call other mixin methods via `self` (e.g.,
  `SessionMixin.login` calls `self._create_2fa_challenge()` from TwoFactorMixin).
- No mixin imports another mixin — all composition happens in `base.py`.
- Each mixin imports only what it needs from the parent `auth` package.


