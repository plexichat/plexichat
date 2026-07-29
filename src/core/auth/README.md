# Authentication Module Structure

This directory contains the refactored authentication module organized into focused submodules.

## Module Overview

| Module | Contents |
|--------|----------|
| `registration.py` | `register`, `register_selftest`, `verify_email`, `resend_verification` |
| `login.py` | `login`, `complete_2fa` |
| `sessions.py` | `verify_token`, `refresh_session`, `create_session_for_user`, `logout`, `logout_all`, `logout_all_users`, `get_sessions`, `revoke_session`, `schedule_account_deletion`, `cancel_account_deletion`, `delay_account_deletion`, `force_purge_account` |
| `account.py` | `schedule_account_deletion`, `cancel_account_deletion`, `delay_account_deletion`, `force_purge_account` |
| `profile.py` | `update_user` |
| `twofa.py` | `setup_2fa`, `confirm_2fa`, `disable_2fa`, `regenerate_backup_codes`, `get_2fa_status` |
| `passkeys.py` | `generate_passkey_registration_options`, `verify_passkey_registration`, `generate_passkey_authentication_options`, `verify_passkey_authentication`, `list_passkeys`, `revoke_passkey`, `rename_passkey` |
| `passwords.py` | `change_password`, `request_password_reset`, `reset_password`, `validate_password` |
| `bots.py` | `create_bot`, `get_bot`, `get_user_bots`, `regenerate_bot_token`, `update_bot_permissions`, `disable_bot`, `enable_bot`, `delete_bot` |
| `tokens.py` | API access tokens CRUD + verify + scopes (`create_api_access_token`, `list_api_access_tokens`, `get_api_access_token`, `update_api_access_token`, `revoke_api_access_token`, `unrevoke_api_access_token`, `rotate_api_access_token`, `add_api_access_token_scope`, `remove_api_access_token_scope`, `list_api_access_token_scopes`, `get_api_access_token_usage`, `verify_api_access_token`, `is_api_access_token_required`) |
| `devices.py` | `get_devices`, `rename_device`, `revoke_device` |
| `ip_blacklist.py` | `block_ip`, `unblock_ip`, `is_ip_blocked`, `get_blocked_ips` |
| `audit.py` | `get_login_history`, `get_security_events` |
| `users.py` | `get_user`, `get_user_by_username`, `get_users_bulk`, `get_user_profiles_bulk`, `grant_permission` |
| `capabilities.py` | `has_capability`, `require_capability` |
| `oauth.py` | `oauth_login` |

## Shared Modules (unchanged)

- `models.py` - Data models (User, Session, Bot, TokenInfo, etc.)
- `exceptions.py` - Custom exceptions
- `permissions.py` - Permission constants and validation
- `manager.py` - AuthManager class (re-exports from managers.base)
- `schema.py` - Database schema creation
- `totp.py` - TOTP utilities
- `passkeys.py` - PasskeyManager class
- `reaper.py` - Account cleanup task

## Usage

### Setup (once at application startup)

```python
from src.core.auth import setup
from src.core.database import Database

db = Database()
db.connect()
setup(db)
```

### Using functions (import from root for convenience)

```python
from src.core import auth

user = auth.register("username", "email@example.com", "password")
result = auth.login("username", "password")
token_info = auth.verify_token(result.token)
```

### Direct submodule imports (for clarity)

```python
from src.core.auth.registration import register, verify_email
from src.core.auth.login import login
from src.core.auth.sessions import verify_token, logout
from src.core.auth.twofa import setup_2fa, confirm_2fa
from src.core.auth.passwords import change_password
```

## Backward Compatibility

All functions are re-exported from `src.core.auth` (the package `__init__.py`), so existing code using `from src.core import auth` or `from src.core.auth import ...` continues to work without changes.