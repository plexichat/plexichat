# Endpoint tester

Use this service to execute individual API endpoint tests, including generic HTTP validation, resource deletion, and standalone feature flows.

## Overview

`EndpointTester` at `plexichat/src/core/selftest/services/endpoints/tester.py:22` composes feature-specific mixins into a single class that `SelfTestRunner` calls during the endpoint phase. The tester validates status codes, builds request bodies from OpenAPI schemas, and records results on `SelfTestContext.results`.

## Mixins

Each mixin inherits from `EndpointTesterBase` at `plexichat/src/core/selftest/services/endpoints/base.py`, which declares `ctx: SelfTestContext` as a typed attribute. This ensures `pyright` recognizes `self.ctx` on every mixin without suppression comments.

| File | Mixin | Responsibilities |
| --- | --- | --- |
| `tester.py` | `EndpointTester` | Compose all mixins and expose the unified API. |
| `base.py` | `EndpointTesterBase` | Define `ctx` type and `__init__` shared by all mixins. |
| `core.py` | `CoreMixin` | Implement `test_endpoint()` for generic HTTP tests, including body construction, multipart handling, retry, and side-effect capture. |
| `auth.py` | `AuthMixin` | Implement `test_auth_endpoints()` for login, registration, session listing, logout, and `revoke-all` flows. |
| `admin.py` | `AdminMixin` | Implement `test_migration_endpoints()`, `test_access_token_rotate()`, and `test_delay_deletion()`. |
| `resources.py` | `ResourceMixin` | Implement `test_delete_resources()` and `test_password_reset_confirm()`. |
| `media.py` | `MediaMixin` | Implement `test_media_upload_complete()` for chunked upload sessions. |
| `polls.py` | `PollMixin` | Implement `test_poll_vote()` and `test_poll_close()`. Call `test_poll_vote()` before `test_poll_close()`. |
| `bots.py` | `BotMixin` | Implement `test_bot_server_integration()` for bot request and approval flows. |
| `features.py` | `FeatureMixin` | Implement `test_dsar()`, `test_transcript_export()`, `test_onboarding_preset()`, and other feature coverage. |
| `applications.py` | `ApplicationMixin` | Implement `test_bot_creation_via_api()` and `test_new_migration_endpoints()`. |
| `twofa.py` | `TwoFAMixin` | Implement `test_user_2fa_flow()` and `test_admin_2fa_flow()`. |
| `interactions.py` | `InteractionMixin` | Implement `test_interaction_callback()` for seeded interaction tokens. |
| `suite.py` | `SuiteMixin` | Implement `test_suite_domains()` for stateful PlexiStash and PlexiSum routes and `test_bulk_delete_messages()`. |
| `extended.py` | `ExtendedCoverageMixin` | Implement `test_negative_permissions()`, `test_pagination_walks()`, `test_concurrency_threads()`, `test_entity_lifecycle()`, `test_websocket_thread_dispatch()`, and `test_oauth_passkeys_federation_stubs()`. |

## Before you begin

Ensure that `SetupService` at `plexichat/src/core/selftest/services/setup/composer.py:19` has completed successfully. The tester depends on context fields populated during setup, such as `test_server_id`, `test_channel_id`, `test_poll_id`, and auth tokens in `ctx.session`. The runner handles this ordering for you in `plexichat/src/core/selftest/runner.py:205`.

## Use the tester

Create the tester with a shared context:

```python
from plexichat.src.core.selftest.context import SelfTestContext
from plexichat.src.core.selftest.services.endpoints import EndpointTester
import requests

ctx = SelfTestContext(base_url="http://localhost:8000", config={}, requests_module=requests)
tester = EndpointTester(ctx)
```

Test a single endpoint in the discovery loop:

```python
# Called for each route discovered by RouteDiscovery
tester.test_endpoint("GET", "/api/v1/users/@me", route_details)
```

Run standalone flows after the main loop. These flows require `ctx.standalone_mode` to be `True`:

```python
tester.test_auth_endpoints()
tester.test_migration_endpoints()
tester.test_access_token_rotate()
tester.test_delay_deletion()
tester.test_media_upload_complete()
tester.test_poll_vote()
tester.test_poll_close()
tester.test_delete_resources()
tester.test_bot_server_integration()
```

## Behavior

`test_endpoint()` at `plexichat/src/core/selftest/services/endpoints/core.py` performs the following steps:

1.  Resolves path parameters through `DataGenerator.get_param_value()` at `plexichat/src/core/selftest/services/data.py:29`.
2.  Builds the request body from the OpenAPI schema through `DataGenerator.get_minimal_body()` at `plexichat/src/core/selftest/services/data.py:337`.
3.  Selects `ctx.session` or `ctx.other_session` based on whether the path appears in `ctx._other_session_paths` or matches special cases such as `/approvals/*/approve` or thread leave.
4.  Sends the request, retries on transient failures, captures IDs such as webhook IDs, and appends the result to `ctx.results`.

All standalone methods except `test_endpoint()`, `test_delete_resources()`, and `test_bot_server_integration()` are gated behind `ctx.standalone_mode`. In non-standalone mode, the runner skips them and defers destructive endpoints. See `plexichat/src/core/selftest/runner.py:373` for the full gating logic.

Note: Each mixin accesses shared state only through `self.ctx`. Mixins do not call methods on other mixins, which keeps the composition simple and avoids circular dependencies.

## Dependencies

*   `SelfTestContext` — shared mutable state including sessions, IDs, and results.
*   `requests` — HTTP client for all endpoint calls.
*   `Pillow (PIL)` — generates test PNG images for media tests.
*   `src.api` — resolves the auth module, database, and internal secrets.
*   `src.utils.encryption` — hashes passwords during auth tests.

## Related documentation

*   [Self-test module](../README.md) — overall execution order.
*   `plexichat/src/core/selftest/services/data.py:337` — schema-to-body conversion that the tester uses.
*   `plexichat/src/core/selftest/runner.py:384` — exclusion and deferral lists that determine which routes the tester executes.
