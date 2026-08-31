# Applications Tests

This directory contains tests for the applications module, which implements
bot/application functionality, slash commands, interactions, and OAuth2 flows.

## Test files

| File | Description |
|------|-------------|
| `test_application_crud.py` | Application create, read, update, and delete operations |
| `test_command_registration.py` | Command registration: chat input, user, message, and guild commands |
| `test_command_options.py` | Command option types: string, integer, boolean, channel, choices, subcommands |
| `test_interaction_handling.py` | Interaction creation, response dispatch, and token binding |
| `test_responses.py` | Interaction response models: pong, message, deferred, modal, and ephemeral |
| `test_components.py` | UI components: buttons, select menus, text inputs, action rows, and modals |
| `test_installation.py` | Application installation on servers and duplicate prevention |
| `test_oauth_flows.py` | OAuth2 authorization URLs, token exchange, and revocation |
| `test_rate_limits.py` | Token bucket rate limiting with shared storage |
| `test_webhooks.py` | Webhook signature verification and timestamp validation |
| `test_integration.py` | End-to-end application workflows |

## Shared fixtures

The `conftest.py` file defines reusable fixtures and factories:

- `app_factory` -- Factory for creating test applications with optional bots.
- `command_factory` -- Factory for creating test commands with various options.
- `interaction_factory` -- Factory for creating command, component, and ping
  interactions.
- `test_application` / `test_command` / `test_interaction` -- Single-instance
  shortcuts for simple tests.

## Key behaviors tested

- **Command scope isolation:** Global and guild commands with the same name
  can coexist without conflict.
- **Interaction token binding:** Tokens are bound to a specific application;
  mismatched application IDs are rejected with a one-shot safety check.
- **Millisecond precision:** Interaction and authorization code expiry use
  millisecond timestamps, not seconds.
- **Rate limiting:** Token buckets use shared storage across manager
  instances; exhausted buckets raise `RateLimitError` with a `retry_after`
  value.
- **OAuth2 code reuse:** Authorization codes are single-use; exchanging a
  code twice raises `InvalidGrantError`.
- **Webhook signature verification:** Signatures include a timestamp; old
  and future timestamps are rejected to prevent replay attacks.
- **Component validation:** Button styles, select menus, and text inputs
  validate their configuration at creation time.
