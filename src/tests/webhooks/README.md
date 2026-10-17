# Webhooks Tests

This directory contains tests for the webhooks module, which handles
webhook creation, execution, management, and security.

## Test files

| File | Description |
|------|-------------|
| `test_create_webhook.py` | Webhook creation: token format, name validation, avatar URLs |
| `test_execute.py` | Webhook execution: send messages, token authentication, URL execution |
| `test_management.py` | Webhook CRUD: get, update, delete, and move between channels |
| `test_permissions.py` | Permission enforcement: create, view, update, delete, and execute |
| `test_token.py` | Token format, validation, regeneration, and secret-only usage |
| `test_embeds.py` | Embed delivery: single, multiple, max limit, and rich embeds |
| `test_overrides.py` | Per-message username and avatar URL overrides |
| `test_threads.py` | Posting webhook messages to threads |
| `test_edge_cases.py` | Error handling and boundary conditions |
| `test_integration.py` | End-to-end webhook workflows |

## Shared fixtures

The `conftest.py` file defines reusable fixtures:

- `base_server_setup` -- Creates a server with an owner, member, and
  non-member for permission tests.
- `fresh_server` -- Creates an isolated server for tests requiring no
  shared state.
- `webhook_with_token` -- Creates a webhook and returns it with its token
  for execution tests.

## Key behaviors tested

- **Token format:** Tokens follow the `webhook.<id>.<secret>` structure;
  secrets are at least 20 characters.
- **Token storage:** Tokens are stored as SHA-256 hashes, not plaintext.
- **Name sanitization:** HTML tags are stripped from webhook names.
- **Avatar validation:** `javascript:` and `data:` URI schemes are rejected.
- **Permission model:** Non-members are denied all management operations;
  anyone with a valid token can execute a webhook.
- **Embed limits:** Maximum 10 embeds per message; exceeding the limit
  raises `EmbedLimitError`.
- **Override isolation:** Per-message username/avatar overrides do not
  modify the webhook's stored settings.
- **Thread support:** Webhooks can post to threads via `thread_id` parameter.
