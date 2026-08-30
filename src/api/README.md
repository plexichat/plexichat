# API module

The `src.api` package implements the FastAPI application layer for the Plexichat server. It exposes the HTTP API, WebSocket gateway, and all request middleware.

## Overview

This module is the entry point for HTTP traffic. You initialize it once in `main.py` with your core dependencies, then run the returned `FastAPI` instance with Uvicorn. All route handlers, middleware, and configuration live under this package.

## Directory structure

```
api/
├── __init__.py               # Module setup and getter functions
├── app.py                    # FastAPI application factory (create_app)
├── config.py                 # APIConfig dataclass and caching
├── dependencies.py           # FastAPI dependency helpers
├── lru_cache.py              # LRU caching utilities
├── media_maintenance.py      # Periodic cleanup for chunked uploads
├── media_serving.py          # Attachment serving route registration
├── poll_expiry.py            # Periodic poll expiry sweep
├── telemetry_maintenance.py  # Periodic telemetry retention sweep
├── middleware/               # ASGI and HTTP middleware
├── routes/                   # Endpoint handlers by resource
├── schemas/                  # Pydantic request and response models
├── websocket/                # WebSocket gateway
└── templates/                # Server-rendered templates
```

## Core files

### `__init__.py` — module setup

Stores references to core modules (`db`, `auth`, `messaging`, `servers`, and others) and exposes getter functions. Call `setup()` once before you call `create_app()`.

Key exports:

| Symbol | Description |
|--------|-------------|
| `setup(db, ...)` | Stores core module references and marks the API as initialized |
| `create_app()` | Re-exported from `app.py`; builds the configured `FastAPI` instance |
| `get_db()` / `get_database()` | Returns the database instance or raises if setup is incomplete |
| `get_auth()`, `get_messaging()`, `get_servers()`, ... | Returns the stored module or falls back to `src.core` imports |
| `is_setup()` | Returns `True` after `setup()` completes |
| `is_self_test_request(request)` | Validates internal requests from localhost using `hmac.compare_digest` |

### `app.py` — application factory

`create_app(enable_rate_limiting=True, enable_docs=True)` creates and configures the `FastAPI` application. It is responsible for:

*   Configuring the `AnyIO` thread limiter from `max_worker_threads`.
*   Registering middleware in the correct order (see [Middleware](#middleware)).
*   Installing the static client middleware, CORS handlers, and exception handlers.
*   Mounting the API router at `config.api_prefix` (default `/api/v1`), the WebSocket gateway at `/gateway`, and the docs router.
*   Running lifespan tasks: presence sweep, poll expiry, media maintenance, telemetry retention, route template registration, cross-worker WebSocket listener, and event queue consumer.

### `config.py` — configuration

`APIConfig` is a dataclass loaded from `utils.config`. Values are cached for 300 seconds. Key fields include `title`, `description`, `version`, `api_prefix`, `cors_origins`, `cors_allow_methods`, `cors_allow_headers`, `max_request_body_size` (default 10 MB), `max_worker_threads` (default 16), and docs URLs. CORS wildcards (`*`) are rejected unless `allow_wildcard_cors` is explicitly set.

Use `get_api_config()` to read the config and `clear_api_config_cache()` to force a reload (useful in tests).

### `dependencies.py` — dependency injection

Re-exports `get_db`, `get_auth`, `get_current_user`, and `get_optional_user` for use with `FastAPI.Depends` in route handlers.

## Middleware

Middleware is registered in `app.py`. In Starlette, the last `add_middleware` call is the outermost layer and runs first. The effective outermost-to-innermost order is:

1.  `LoggingMiddleware` — complete request timing and server-side telemetry
2.  `IPBlockingMiddleware` — rejects blacklisted IPs before any other work
3.  `CORSMiddleware` and `CORSPreflightMiddleware` — handles CORS and `OPTIONS` preflight
4.  `DatabaseMiddleware` — attaches a database session to each request
5.  `SecurityHeadersMiddleware` — adds security headers to all responses
6.  `MaxBodySizeMiddleware` — enforces `max_request_body_size` with chunk-aware byte counting (larger caps for `/suite/sum/` and `/suite/stash/` uploads)
7.  `AuthenticationMiddleware` — validates bearer and cookie tokens, marks internal requests
8.  `RateLimitMiddleware` (ASGI) — enforces per-user and per-IP rate limits

See `middleware/README.md` for details on each middleware.

## Routes

Routes are defined under `routes/` and assembled in `routes/__init__.py` by `create_api_router()`. The router mounts handlers at `config.api_prefix` and includes sub-routers for authentication, users, servers, channels, messages, relationships, and others. The docs router is mounted separately. See `routes/README.md` for the full route table.

## Schemas

Pydantic models under `schemas/` validate requests and serialize responses. Shared primitives such as `SnowflakeID` live in `schemas/common.py`.

## WebSocket

The gateway under `websocket/` handles the persistent WebSocket connection at `/gateway`. See `websocket/` for opcodes, intents, session management, and event dispatching.

## Quickstart

Initialize the module in your server entry point, then start the server:

```python
from src.api import setup, create_app
import uvicorn

# Call once at startup with your core modules
setup(
    db=db,
    auth_module=auth,
    messaging_module=messaging,
    servers_module=servers,
)

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Access core modules from any route handler:

```python
import src.api as api

auth = api.get_auth()
messaging = api.get_messaging()
servers = api.get_servers()
```

## Related documentation

*   `middleware/README.md` — request middleware reference
*   `routes/README.md` — endpoint routing reference
*   `schemas/` — request and response model reference
*   `websocket/` — gateway reference
