# API tests

This directory contains tests for Plexichat API routes, middleware, and error
handlers. All tests use a FastAPI `TestClient` and are function-scoped.

## Test files

| File | Description |
| --- | --- |
| `test_auth_routes.py` | Registration and login endpoints |
| `test_authentication.py` | Authentication middleware |
| `test_channel_routes.py` | Channel CRUD endpoints |
| `test_error_handling.py` | Error handler responses |
| `test_health.py` | Health check endpoint |
| `test_message_routes.py` | Message CRUD endpoints |
| `test_presence_routes.py` | Presence status endpoints |
| `test_reaction_routes.py` | Reaction endpoints |
| `test_relationship_routes.py` | Friend/block relationship endpoints |
| `test_server_routes.py` | Server management endpoints |
| `test_user_routes.py` | User profile endpoints |
| `test_webhook_routes.py` | Webhook management endpoints |

## Running the tests

Run all API tests:

```bash
pytest src/tests/api/ -v
```

Run tests for a specific route:

```bash
pytest src/tests/api/test_auth_routes.py -v
```

## Fixtures

Fixtures are defined in `conftest.py`. Key fixtures include:

- **`auth_headers`** -- Returns `{"Authorization": "Bearer <token>"}` for
  authenticated requests.
- **`second_auth_headers`** -- Returns authorization headers for a second
  test user.
- **`second_test_user`** -- Registers and logs in a second user. Returns a
  dict with `user`, `token`, `username`, and `password`.
- **`db_and_modules`** -- Legacy fixture that returns a dict of module
  instances for backward compatibility with older tests.
