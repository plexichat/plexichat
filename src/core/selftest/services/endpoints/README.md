# Endpoint Tester

## Purpose
Executes individual API endpoint tests, DELETE resource tests,
standalone auth/admin/media/poll flows, and bot-server integration.

## Primary Responsibilities
- Execute single HTTP endpoint tests with body construction and retry
- Test DELETE on tracked resources (messages, webhooks, channels, etc.)
- Run standalone auth flows (login, register, sessions, logout, revoke-all)
- Test admin operations (migrations, token rotation, delay-deletion)
- Test chunked media upload (create session, chunk, complete)
- Test poll vote and close cycles
- Test bot server integration (request, approve)

## Architecture (Mixin-based)
The `EndpointTester` class is composed via mixins:

| File | Mixin | Responsibility |
|------|-------|---------------|
| `tester.py` | `EndpointTester` | Composition orchestrator |
| `base.py` | `EndpointTesterBase` | Typed `self.ctx` attribute + `__init__` |
| `core.py` | `CoreMixin` | `test_endpoint` -- generic HTTP test with body construction, multipart, retry |
| `auth.py` | `AuthMixin` | `test_auth_endpoints` -- login, register, sessions, logout, revoke-all |
| `admin.py` | `AdminMixin` | `test_migration_endpoints`, `test_access_token_rotate`, `test_delay_deletion` |
| `resources.py` | `ResourceMixin` | `test_delete_resources`, `test_password_reset_confirm` |
| `media.py` | `MediaMixin` | `test_media_upload_complete` |
| `polls.py` | `PollMixin` | `test_poll_vote`, `test_poll_close` |
| `bots.py` | `BotMixin` | `test_bot_server_integration` |

### Pyright Compatibility
`EndpointTesterBase` declares `ctx: SelfTestContext` as a class-level typed
attribute. Every mixin inherits from this base class, so pyright sees
`self.ctx` as a known, typed attribute on every `self` reference. No
`# type: ignore` comments or file-level suppressions are needed.

## Usage

```python
from src.core.selftest.services.endpoints import EndpointTester
from src.core.selftest.context import SelfTestContext

tester = EndpointTester(ctx)

# Generic endpoint test (called by runner in a loop)
tester.test_endpoint("GET", "/api/v1/users/@me", route_details)

# Standalone flows (only in standalone_mode)
tester.test_auth_endpoints()
tester.test_migration_endpoints()
tester.test_access_token_rotate()
tester.test_media_upload_complete()
tester.test_poll_vote()
tester.test_poll_close()

# Resource cleanup
tester.test_delete_resources()

# Bot integration
tester.test_bot_server_integration()
```

## Dependencies
- `SelfTestContext` -- shared mutable state (sessions, IDs, results)
- `requests` library for HTTP calls
- `PIL` (Pillow) for generating test PNG images
- `src.api` for auth module, DB, and internal-secret access
- `src.utils.encryption` for password hashing during auth tests

## Notes
- All methods are gated behind `self.ctx.standalone_mode` except `test_endpoint`,
  `test_delete_resources`, and `test_bot_server_integration`.
- `test_endpoint` is the most complex method -- it constructs multipart bodies,
  form data, JSON bodies, handles retry logic, and captures side effects
  (webhook IDs, auto-unblock).
- Each mixin only accesses `self.ctx` -- no cross-mixin method calls exist,
  keeping the design simple.
- `test_poll_vote` must run before `test_poll_close`.
