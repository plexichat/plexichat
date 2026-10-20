# Auth managers

This directory contains the `AuthManager` composition class and its mixins. Use `AuthManager` to handle registration, sessions, two-factor authentication (2FA), passwords, bots, API tokens, profiles, devices, IP blocking, auditing, OAuth, account deletion, and passkeys. `AuthManager` composes all mixins into one public class so you call a single API.

## File layout

Each file defines one mixin or the composition class. Do not import mixins directly.

| File | Mixin or class | Responsibility |
| --- | --- | --- |
| `base.py` | `AuthManager` | Compose all mixins and provide shared helpers (`__init__`, `_log_audit`, `_track_ip`, `_track_device`, `_encrypt_ua`, `_ua_index`, serialization, config access, `_ensure_system_user`, `_get_timestamp`) |
| `registration.py` | `RegistrationMixin` | Register users, verify email, resend verification |
| `session.py` | `SessionMixin` | Log in, create sessions, verify tokens, refresh or revoke sessions, handle failed logins |
| `twofactor.py` | `TwoFactorMixin` | Create 2FA challenges and manage setup, confirmation, disable, and backup codes |
| `password.py` | `PasswordMixin` | Change passwords, request and reset passwords, validate password strength |
| `bot.py` | `BotMixin` | Create, fetch, list, update, disable, enable, and delete bots and bot tokens |
| `api_token.py` | `ApiTokenMixin` | Create and verify API access tokens, manage scopes, record events |
| `profile.py` | `ProfileMixin` | Update and fetch user profiles, manage permissions, cache user data |
| `device.py` | `DeviceMixin` | List, rename, and revoke devices |
| `ip_blacklist.py` | `IpBlacklistMixin` | Block, unblock, and query blocked IP addresses |
| `audit.py` | `AuditMixin` | Query login history and security events |
| `oauth.py` | `OAuthMixin` | Check capabilities and handle OAuth login (`has_capability`, `require_capability`, `oauth_login`) |
| `deletion.py` | `DeletionMixin` | Schedule, cancel, delay, and purge account deletions |
| `passkey.py` | `PasskeyMixin` | Delegate passkey operations to `PasskeyManager` |
| `protocol.py` | `AuthManagerProtocol` | Declare cross-mixin attributes for static type checking |

## Before you begin

You need:

- A connected database instance (`Database`).
- An optional email sender for verification emails.
- `authentication` and `encryption` keys in your config.

`AuthManager` depends on:

- `src.core.base.BaseManager` — Provides `self._db`, `_generate_id()`, and `_user_exists()`.
- `src.utils.encryption.EncryptionManager` — Hashes passwords with Argon2, encrypts data, and creates blind indexes.
- `src.core.auth.models` — Defines `User`, `Session`, `TokenInfo`, `AuthResult`, `AuthStatus`, `AccountType`, and `AuditEventType`.
- `src.core.auth.exceptions` — Defines all exceptions listed in [Handle errors](#handle-errors).
- `src.core.auth.permissions` — Defines scopes in `PERMISSIONS` (`src/core/auth/permissions.py:42`) and helpers `has_permission`, `validate_permissions`, `merge_permissions`. Wildcard scopes such as `messages.*` are supported; the server-role layer rejects wildcards (`src/api/routes/admin/roles.py:200`).
- `src.core.auth.blacklist.BlacklistManager` — Blocks IPs.
- `src.core.auth.deletion_log.DeletionLog` — Tracks account deletion events.
- `src.core.auth.passkeys.PasskeyManager` — Implements WebAuthn/FIDO2.
- `src.core.auth.tokens` — Creates, parses, and verifies session and bot tokens.
- `src.core.database` — Provides the `cached` decorator and `invalidate_pattern` for cache management.

## Use AuthManager

### 1. Create the manager

```python
from src.core.auth.manager import AuthManager

auth_mgr = AuthManager(db, email_sender=smtp_sender)
```

`AuthManager.__init__` sets `self._db`, `self.crypto` (`EncryptionManager` with Argon2 settings), `self._config`, `self.blacklist` (`BlacklistManager`), `self.deletion_log`, and `self.passkeys` (`PasskeyManager`), then ensures the system user exists.

### 2. Register and verify a user

```python
result = auth_mgr.register(username="alice", email="alice@example.com", password="SecurePass123!")
# result is an AuthResult with status == AuthStatus.SUCCESS
auth_mgr.verify_email(user_id=result.user.id, token="email-token")
```

### 3. Log in and verify a token

```python
result = auth_mgr.login(
    username="alice",
    password="SecurePass123!",
    device_info={"fingerprint": "abc123", "type": "mobile"},
    ip_address="192.168.1.1",
)
# If 2FA is enabled, result.status == AuthStatus.TWO_FACTOR_REQUIRED and result contains a challenge_token

info = auth_mgr.verify_token("session_token_here", ip_address="192.168.1.1")
# info is a TokenInfo with account_id, permissions, and rate_limit_tier
```

OAuth login respects 2FA. If the user has TOTP enabled, `oauth_login` returns `TWO_FACTOR_REQUIRED` instead of a session. PKCE is enforced at the API layer: `GET /api/v1/auth/oauth/{provider}/login` requires `code_challenge` with `code_challenge_method=S256` (`src/api/routes/auth/oauth.py:122`).

### 4. Manage sessions and passwords

```python
sessions = auth_mgr.get_sessions(user_id=42)
auth_mgr.revoke_session(user_id=42, session_id=sessions[0].id)

auth_mgr.request_password_reset(email="alice@example.com")
auth_mgr.reset_password(user_id=42, reset_token="token", new_password="NewSecurePass456!")
```

### 5. Check capabilities

```python
from src.core.auth.exceptions import PermissionDeniedError

# Check without raising
if auth_mgr.has_capability(token_info, "messages.send"):
    print("Can send messages")

# Require or raise PermissionDeniedError
auth_mgr.require_capability(token_info, "servers.manage")
```

Available scopes are defined in `PERMISSIONS` (`src/core/auth/permissions.py:42`) and include `messages.*`, `conversations.*`, `servers.*`, `channels.*`, `roles.*`, `voice.*`, `video.*`, `account.*`, `bots.*`, `moderation.*`, and `admin.*`. Default user and bot grants are `DEFAULT_USER_PERMISSIONS` and `DEFAULT_BOT_PERMISSIONS`.

## Handle errors

All operations raise domain-specific exceptions from `src.core.auth.exceptions`. Catch the specific error you want to handle:

| Exception | When it occurs |
| --- | --- |
| `InvalidCredentialsError` | You provide a wrong username, email, or password |
| `AccountLockedError` | The account is locked due to failed attempts or scheduled deletion. Check `e.locked_until`. |
| `EmailNotVerifiedError` | You attempt to log in before verifying email |
| `TokenExpiredError` | The session or bot token has expired |
| `TokenInvalidError` | The token is malformed, revoked, or fails binding checks (IP or User-Agent mismatch). Also raised as `TokenInvalidError("Token verification rate limit exceeded")` when you hit the rate limit. |
| `TwoFactorInvalidError` | You provide an invalid 2FA code or backup code |
| `UserExistsError` | You register with a taken username or email |
| `UserNotFoundError` | User lookup returns no results |
| `WeakPasswordError` | The password does not meet strength requirements |
| `InvalidUsernameError` | The username violates format rules |
| `InvalidEmailError` | The email fails validation |
| `PermissionDeniedError` | The user lacks the required capability for an operation |
| `BotLimitExceededError` | The user has reached the maximum number of bots |

Example:

```python
from src.core.auth.exceptions import InvalidCredentialsError, AccountLockedError, EmailNotVerifiedError

try:
    result = auth_mgr.login(username, password)
except InvalidCredentialsError:
    print("Invalid username or password")
except AccountLockedError as e:
    print(f"Account locked until: {e.locked_until}")
except EmailNotVerifiedError:
    print("Please verify your email first")
```

## Understand composition order

`AuthManager` in `base.py` inherits from `BaseManager` and then all mixins. The method resolution order (MRO) is:

```
AuthManager -> BaseManager -> RegistrationMixin -> SessionMixin ->
TwoFactorMixin -> PasswordMixin -> BotMixin -> ApiTokenMixin ->
ProfileMixin -> DeviceMixin -> IpBlacklistMixin -> AuditMixin ->
OAuthMixin -> DeletionMixin -> PasskeyMixin
```

Shared helpers (`_log_audit`, `_track_ip`, `_track_device`, `_encrypt_ua`, `_ua_index`, `_get_timestamp`, and others) live on `AuthManager` in `base.py`, so they take priority over any mixin definitions.

## Follow design rules

- Access shared state through `self._db`, `self._config`, `self.crypto`, `self.blacklist`, `self.deletion_log`, and `self.passkeys` (all set in `AuthManager.__init__`). Do not re-initialize them in mixins.
- Call any mixin method through `self` (for example, `SessionMixin.login` calls `self._create_2fa_challenge()` from `TwoFactorMixin`).
- Do not import one mixin from another. Compose all mixins only in `base.py`.
- Declare cross-mixin attributes in `protocol.py` (`AuthManagerProtocol`) so type checkers can resolve them without suppression comments.
- Each mixin imports only what it needs from the parent `auth` package.
- For server roles, do not send wildcard permissions such as `*` or `messages.*`. The roles API rejects them with `InvalidPermissionKey` (`src/api/routes/servers/roles.py:200`); use explicit scopes from `PERMISSIONS`.

## Public API

The public API does not change when you use mixins. Import `AuthManager` from the same locations:

```python
from src.core.auth.manager import AuthManager
from src.core import auth
```
