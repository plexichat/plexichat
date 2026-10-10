# Setup service

Use this service to create and configure the test data that endpoint validation requires.

## Overview

`SetupService` at `plexichat/src/core/selftest/services/setup/composer.py:19` composes feature-specific mixins into a single class. Call `run_setup()` to create users, servers, channels, and supporting resources in the correct order.

## Mixins

Each mixin inherits from `SetupServiceBase` at `plexichat/src/core/selftest/services/setup/base.py`, which declares `ctx: SelfTestContext` as a typed attribute. This gives `pyright` a known type for `self.ctx` without suppression comments.

| File | Mixin | Responsibilities |
| --- | --- | --- |
| `composer.py` | `SetupService` | Orchestrate the full setup flow and return `True` on success. |
| `base.py` | `SetupServiceBase` | Define `ctx` type, `__init__`, and shared helpers such as `_ensure_password()`. |
| `auth.py` | `AuthSetupMixin` | Configure security headers, create the main admin user and two additional test users, handle login, register the secondary user, and block the `selftest_` username prefix. |
| `admin.py` | `AdminSetupMixin` | Grant admin permissions, sync `admin_users`, create admin roles (`super_admin`, `support_admin`, `moderation_admin`, `test`), assign roles, and ensure approval tables exist. |
| `server.py` | `ServerSetupMixin` | Create the test server, channel, message, conversation, role, invite, and webhook, and join the secondary user to the server through an invite. |
| `media.py` | `MediaSetupMixin` | Create a dummy media file and resolve log filenames for media tests. |
| `features.py` | `FeatureSetupMixin` | Create settings, applications and bots, notifications, reports (user, hash, message), automod rules and violations, access tokens, support tickets, polls, friend requests, emoji, stickers, threads, and PlexiJoin resources. |

## Before you begin

Ensure the following conditions are met:

*   The database and `auth` and `servers` modules are available through `src.api` at `plexichat/src/core/selftest/services/setup/composer.py:38`. `run_setup()` returns `False` if either module is unavailable.
*   The shared context at `plexichat/src/core/selftest/context.py:11` has `base_url`, `config`, and `requests_module` set.

## Use the service

Create the service with a shared context and call the single entry point:

```python
from plexichat.src.core.selftest.context import SelfTestContext
from plexichat.src.core.selftest.services.setup import SetupService
import requests

ctx = SelfTestContext(base_url="http://localhost:8000", config={}, requests_module=requests)
service = SetupService(ctx)

if not service.run_setup():
    raise RuntimeError("Setup failed")
```

The call sequence inside `run_setup()` at `plexichat/src/core/selftest/services/setup/composer.py:34` is fixed:

1.  Set up security headers and ensure the test password exists.
2.  Create the main user and optionally grant admin roles, then create the second and third test users.
3.  Log in the main and secondary users, configure session headers, and block the `selftest_` prefix.
4.  Create the server, channel, message, role, invite, and webhook, and join the secondary user.
5.  Seed media, settings, applications, reports, automod, tokens, tickets, polls, and other feature resources.
6.  Optionally seed PlexiJoin resources when `ctx.unlicensed` is `False`.
7.  Ensure the `admin_approval_comments` table exists.

## Behavior

*   The service does not wrap setup in a single database transaction. The server runs in a background thread and SQLite serializes writes across connections. Holding a transaction in the main thread while issuing HTTP calls to the background thread causes a deadlock. See the note at `plexichat/src/core/selftest/services/setup/README.md` context in `composer.py:34`.
*   Each mixin method is idempotent where possible. It checks for existing resources before creating new ones, so you can rerun setup after a partial failure.
*   PlexiJoin seeding is license-gated. When `ctx.unlicensed` is `True`, `run_setup()` skips `create_test_plexijoin_resources()` because the licensed endpoint returns `403` on an unlicensed install. See `plexichat/src/core/selftest/services/setup/composer.py:91`.

## Dependencies

*   `SelfTestContext` — holds `test_server_id`, `test_channel_id`, tokens, and other IDs that later phases read.
*   `requests` — executes HTTP calls to create users and log in.
*   `src.api` — provides database access, module references, and internal secrets.
*   `src.utils.encryption` — hashes passwords for user creation.
*   `src.core.automod`, `src.core.polls`, `src.core.threads` — create feature-specific resources when seeding fails through the HTTP path.

## Related documentation

*   [Self-test module](../README.md) — overall execution order and configuration.
*   `plexichat/src/core/selftest/context.py:11` — all context fields populated during setup, including `test_other_user_id` and `test_thread_id`.
*   `plexichat/src/core/selftest/services/cleanup.py:23` — pre-test cleanup that runs before setup to remove stale `selftest_%` data.
