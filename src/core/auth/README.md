# Authentication Module

Secure authentication system for PlexiChat API supporting user accounts, bot accounts, two-factor authentication, and granular permissions.

## Features

- User registration and login with secure password hashing (Argon2id)
- Server-side session tokens (not JWT) for instant revocation
- Two-factor authentication (TOTP) compatible with Google Authenticator
- Backup codes for 2FA recovery
- Bot accounts with restricted permissions
- Device and IP tracking
- Comprehensive audit logging
- Granular permission system with wildcards
- Account lockout after failed attempts

## Installation

Requires the following packages:

```bash
pip install pyotp qrcode pillow
```

## Setup

```python
from src.core.database import Database
from src.core import auth

# Initialize database
db = Database()
db.connect()

# Initialize auth (creates tables automatically)
auth.setup(db)

# Optional: with email sender for verification
auth.setup(db, email_sender=my_email_sender)
```

## Usage

### User Registration

```python
from src.core import auth

user = auth.register(
    username="alice",
    email="alice@example.com",
    password="SecurePassword123!"
)
```

### User Login

```python
result = auth.login(
    username="alice",
    password="SecurePassword123!",
    device_info={"fingerprint": "abc123", "name": "Desktop", "type": "desktop"},
    ip_address="192.168.1.1"
)

if result.status == auth.AuthStatus.SUCCESS:
    token = result.token
    user = result.user
elif result.status == auth.AuthStatus.TWO_FACTOR_REQUIRED:
    # Need to complete 2FA
    challenge_token = result.challenge_token
```

### Verify Token (on every request)

```python
try:
    token_info = auth.verify_token(token, ip_address="192.168.1.1")
    user_id = token_info.user_id
    permissions = token_info.permissions
except auth.TokenInvalidError:
    # Invalid or revoked token
    pass
except auth.TokenExpiredError:
    # Token expired
    pass
```

### Check Permissions

```python
token_info = auth.verify_token(token)

# Check permission
if auth.has_capability(token_info, "messages.send"):
    # User can send messages
    pass

# Require permission (raises PermissionDeniedError if missing)
auth.require_capability(token_info, "bots.create")
```

### Two-Factor Authentication

```python
# Setup 2FA
setup = auth.setup_2fa(user_id)
print(f"Secret: {setup.secret}")
print(f"QR URI: {setup.qr_uri}")
print(f"Backup codes: {setup.backup_codes}")

# Confirm 2FA with code from authenticator app
auth.confirm_2fa(user_id, "123456")

# Complete login with 2FA
result = auth.complete_2fa(challenge_token, "123456")

# Disable 2FA
auth.disable_2fa(user_id, password="...", code="123456")

# Regenerate backup codes
new_codes = auth.regenerate_backup_codes(user_id, password="...")
```

### Bot Accounts

```python
# Create bot
bot = auth.create_bot(
    owner_id=user_id,
    username="my_bot",
    display_name="My Bot",
    permissions={"messages.send": True, "messages.read": True}
)
bot_token = bot.token  # Save this - only shown once!

# Use bot token
token_info = auth.verify_token(bot_token)
assert token_info.token_type == "bot"

# Regenerate bot token
new_token = auth.regenerate_bot_token(owner_id, bot_id)

# Disable/enable bot
auth.disable_bot(owner_id, bot_id)
auth.enable_bot(owner_id, bot_id)

# Delete bot
auth.delete_bot(owner_id, bot_id)
```

### Session Management

```python
# Get active sessions
sessions = auth.get_sessions(user_id)

# Revoke specific session
auth.revoke_session(user_id, session_id)

# Logout current session
auth.logout(token)

# Logout all sessions except current
auth.logout_all(user_id, except_token=current_token)
```

### Device Management

```python
# Get known devices
devices = auth.get_devices(user_id)

# Rename device
auth.rename_device(user_id, device_id, "Work Laptop")

# Revoke device (logs out all sessions)
auth.revoke_device(user_id, device_id)
```

### Audit Log

```python
# Get login history
history = auth.get_login_history(user_id, limit=50)

# Get all security events
events = auth.get_security_events(user_id, limit=50)
```

## Configuration

All settings are in `config/config.yaml` under `authentication`:

```yaml
authentication:
  accounts:
    allow_registration: true
    require_email_verification: false
    max_bots_per_user: 5
    username_min_length: 3
    username_max_length: 32
    
  sessions:
    token_bytes: 32
    expire_hours: 168  # 7 days
    max_per_user: 10
    extend_on_activity: true
    
  security:
    max_failed_attempts: 5
    lockout_duration_minutes: 15
    
  totp:
    issuer: PlexiChat
    digits: 6
    interval: 30
    backup_code_count: 10
    
  password:
    min_length: 12
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
    
  bots:
    token_bytes: 48
    require_owner_2fa: false
```

## Permission System

Permissions are hierarchical with wildcard support:

```python
# Specific permission
"messages.send"

# Wildcard - all message permissions
"messages.*"

# Full admin
"*"
```

### Available Permissions

| Category | Permission | Description |
|----------|------------|-------------|
| messages | messages.send | Send messages |
| messages | messages.read | Read messages |
| messages | messages.edit | Edit own messages |
| messages | messages.delete | Delete own messages |
| conversations | conversations.create | Create conversations |
| conversations | conversations.join | Join conversations |
| voice | voice.join | Join voice channels |
| voice | voice.initiate | Start voice calls |
| bots | bots.create | Create bot accounts |
| admin | admin.* | Administrative access |

### Bot Restrictions

Bots cannot have these permissions:
- bots.create
- bots.manage
- account.delete
- admin.*

## Token Format

### User Session Token
```
<session_id>.<random_secret>
Example: 7891234567890123456.a8Kj2mNpQrStUvWxYz...
```

### Bot Token
```
bot.<bot_id>.<random_secret>
Example: bot.7891234567890123456.a8Kj2mNpQrStUvWx...
```

## Security Features

1. **Password Hashing**: Argon2id (OWASP recommended)
2. **Token Storage**: Only SHA-256 hash stored, not the token itself
3. **Constant-time Comparison**: Prevents timing attacks
4. **Account Lockout**: Configurable failed attempt limits
5. **Session Limits**: Maximum concurrent sessions per user
6. **2FA**: TOTP with backup codes
7. **Audit Logging**: All security events logged

## Error Handling

All auth errors inherit from `AuthError`:

```python
from src.core.auth import (
    AuthError,
    InvalidCredentialsError,
    AccountLockedError,
    TokenExpiredError,
    TokenInvalidError,
    TwoFactorRequiredError,
    PermissionDeniedError,
    UserExistsError,
    WeakPasswordError,
)

try:
    auth.login(username, password)
except AccountLockedError as e:
    print(f"Locked until: {e.locked_until}")
except InvalidCredentialsError:
    print("Wrong username or password")
except TwoFactorRequiredError as e:
    print(f"2FA required, challenge: {e.challenge_token}")
```

## Testing

```bash
pytest src/tests/auth/ -v
```
