# Auth

This document describes the authentication module for Plexichat. Use it to register users, authenticate sessions, and manage two-factor authentication, passkeys, bots, and tokens.

## Overview

The auth package is split into focused submodules that each own a small surface. All public functions are re-exported from `src.core.auth`, so you can import from the package root or from a submodule directly. The underlying state is owned by `AuthManager` in `managers/base.py` and accessed through `auth.setup(db)`.

## Module layout

| Module | Exported functions |
| --- | --- |
| `registration.py` | `register`, `register_selftest`, `verify_email`, `resend_verification` |
| `login.py` | `login`, `complete_2fa` |
| `sessions.py` | `verify_token`, `refresh_session`, `create_session_for_user`, `logout`, `logout_all`, `logout_all_users`, `get_sessions`, `revoke_session`, `create_ws_ticket_for_session`, `schedule_account_deletion`, `cancel_account_deletion`, `delay_account_deletion`, `force_purge_account` |
| `account.py` | `schedule_account_deletion`, `cancel_account_deletion`, `delay_account_deletion`, `force_purge_account` |
| `profile.py` | `update_user` |
| `twofa.py` | `setup_2fa`, `confirm_2fa`, `disable_2fa`, `regenerate_backup_codes`, `get_2fa_status` |
| `passkeys.py` | `generate_passkey_registration_options`, `verify_passkey_registration`, `generate_passkey_authentication_options`, `verify_passkey_authentication`, `list_passkeys`, `revoke_passkey`, `rename_passkey` |
| `passwords.py` | `change_password`, `request_password_reset`, `reset_password`, `validate_password` |
| `bots.py` | `create_bot`, `get_bot`, `get_user_bots`, `regenerate_bot_token`, `update_bot_permissions`, `disable_bot`, `enable_bot`, `delete_bot` |
| `tokens.py` | `create_api_access_token`, `list_api_access_tokens`, `get_api_access_token`, `update_api_access_token`, `revoke_api_access_token`, `unrevoke_api_access_token`, `rotate_api_access_token`, `add_api_access_token_scope`, `remove_api_access_token_scope`, `list_api_access_token_scopes`, `get_api_access_token_usage`, `verify_api_access_token`, `is_api_access_token_required` |
| `devices.py` | `get_devices`, `rename_device`, `revoke_device`, `is_trusted_device`, `trust_device`, `untrust_device` |
| `ip_blacklist.py` | `block_ip`, `unblock_ip`, `is_ip_blocked`, `get_blocked_ips` |
| `audit.py` | `get_login_history`, `get_security_events` |
| `users.py` | `get_user`, `get_user_by_username`, `get_users_bulk`, `get_user_profiles_bulk`, `grant_permission` |
| `capabilities.py` | `has_capability`, `require_capability` |
| `oauth` | `oauth_login` |

Shared modules that are not part of the per-feature table:

*   `models.py` — Data models `User`, `Session`, `Bot`, `AccessToken`, `Device`, `KnownIP`, `AuditEntry`, `TokenInfo`, and related enums.
*   `exceptions.py` — Typed exceptions such as `InvalidCredentialsError` and `PermissionDeniedError`.
*   `permissions.py` — Permission constants and helpers `has_permission`, `validate_permissions`.
*   `manager.py` — Re-exports `AuthManager` from `managers/base.py`.
*   `schema.py` — Creates the auth tables.
*   `totp.py` — TOTP utilities.
*   `reaper.py` — Background account cleanup.
*   `services/` and `repositories/` — Internal service and repository layers used by `AuthManager`.

Note: `sessions.py` and `account.py` both export the account-deletion helpers. `sessions.py` is the canonical import path re-exported from `src.core.auth`.

## Setup

Call `setup` once at startup before you call any other auth function.

```python
from src.core.auth import setup
from src.core.database import Database

db = Database()
db.connect()
setup(db)
```

To enable email verification, pass an `email_sender` that implements `EmailSender.send(to, subject, body, html)`.

```python
from src.core.auth import setup

setup(db, email_sender=my_sender)
```

`setup` initializes `AuthManager` and configures the OAuth state manager in `src.core.auth.oauth.state` with the `oauth` config for state TTL settings. If `setup` has not been called, any auth function raises `RuntimeError`.

## Use auth functions

### Import from the package root

Use the package root when you want a stable import path that does not depend on the internal layout.

```python
from src.core import auth

user = auth.register("username", "email@example.com", "password")
result = auth.login("username", "password")
token_info = auth.verify_token(result.token)
```

### Import from a submodule

Use a submodule import when you want an explicit dependency.

```python
from src.core.auth.registration import register, verify_email
from src.core.auth.login import login
from src.core.auth.sessions import verify_token, logout
from src.core.auth.twofa import setup_2fa, confirm_2fa
from src.core.auth.passwords import change_password
```

### Sessions and tokens

```python
from src.core.auth import verify_token, refresh_session, logout, get_sessions

info = verify_token(token)
new_session = refresh_session(token)
sessions = get_sessions(user_id=123)
logout(token)
```

### Two-factor authentication

```python
from src.core.auth import setup_2fa, confirm_2fa, get_2fa_status

setup = setup_2fa(user_id=123)
# Show setup.qr_uri to the user, then confirm with a code
confirm_2fa(user_id=123, code="123456")
status = get_2fa_status(user_id=123)
```

### Passkeys

```python
from src.core.auth import generate_passkey_registration_options, verify_passkey_registration

options = generate_passkey_registration_options(user_id=123)
verify_passkey_registration(user_id=123, credential=credential)
```

## Backward compatibility

All functions listed in the module layout are re-exported from `src.core.auth` (`__init__.py`). Code that uses `from src.core import auth` or `from src.core.auth import ...` continues to work without changes after the split into submodules.

## Related code

*   `plexichat/src/core/auth/__init__.py:197` — `setup`
*   `plexichat/src/core/auth/managers/base.py` — `AuthManager`
*   `plexichat/src/core/auth/exceptions.py` — Auth exceptions
*   `plexichat/src/core/auth/models.py` — `User`, `Session`, `TokenInfo`, `AuthResult`
*   `plexichat/src/core/auth/permissions.py` — `PERMISSIONS`, `has_permission`
