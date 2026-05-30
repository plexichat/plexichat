# Setup Service

## Purpose
Creates and configures test users, servers, channels, messages, roles,
invites, applications, bots, and all other resources for endpoint testing.

## Primary Responsibilities
- Create main test user (admin) and other test user (non-admin)
- Grant admin permissions and set up admin roles (super_admin, support_admin, moderation_admin)
- Login both users and obtain auth tokens
- Create test server, channel, message, role, invite, webhook
- Join other user to server via invite
- Create dummy media file and resolve log filenames
- Create test settings, application, and bot
- Create test notification, reports (user, hash, message)
- Create automod rule, access token, support ticket
- Create poll with options, friend request, emoji, sticker, thread
- Block selftest_ username prefix in username blacklist
- Ensure admin_approval_comments table exists

## Architecture (Mixin-based)
The `SetupService` class is composed via mixins:

| File | Mixin | Responsibility |
|------|-------|---------------|
| `composer.py` | `SetupService` | Orchestrates the full setup flow |
| `base.py` | `SetupServiceBase` | Typed `self.ctx` attribute + `__init__` |
| `auth.py` | `AuthSetupMixin` | Security headers, main user creation, login, other user registration, username blacklist |
| `admin.py` | `AdminSetupMixin` | Admin grant, admin_users sync, admin roles (super/support/moderation/test), role assignments, approvals, approval_comments table |
| `server.py` | `ServerSetupMixin` | Server creation, channel, message, role, invite, webhook, server join |
| `media.py` | `MediaSetupMixin` | Dummy media file creation, log filename resolution |
| `features.py` | `FeatureSetupMixin` | Settings, apps/bots, notifications, reports, automod, access tokens, tickets, polls, friend requests, emoji, stickers, threads |

### Pyright Compatibility
`SetupServiceBase` declares `ctx: SelfTestContext` as a class-level typed
attribute. Every mixin inherits from this base class, so pyright sees
`self.ctx` as a known, typed attribute on every `self` reference. No
`# type: ignore` comments or file-level suppressions are needed.

## Usage

```python
from src.core.selftest.services.setup import SetupService
from src.core.selftest.context import SelfTestContext

service = SetupService(ctx)
service.run_setup()
```

## Dependencies
- `SelfTestContext` -- shared mutable state (sessions, IDs, results)
- `requests` library for HTTP calls
- `src.api` for module references, DB access, and internal-secret
- `src.utils.encryption` for password hashing
- `src.core.automod` / `src.core.polls` / `src.core.threads` for feature resources

## Notes
- The setup intentionally does NOT wrap in a single DB transaction.
  The server runs in a background thread, and SQLite serializes writes
  across connections. Holding a transaction in the main thread while
  making HTTP calls to the background thread would deadlock.
- The `run_setup()` method is the single entry point that orchestrates
  all setup phases in the correct order.
- Each mixin method is idempotent where possible -- it checks for existing
  resources before creating new ones.
