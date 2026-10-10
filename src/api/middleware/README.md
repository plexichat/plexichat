# Middleware

This directory contains ASGI and HTTP middleware that processes every request before it reaches a route handler and every response before it is sent to the client.

## Overview

Middleware handles cross-cutting concerns such as authentication, rate limiting, error formatting, and observability. All middleware is registered in `src.api.app.create_app()`. In Starlette, the last middleware you add is the outermost layer and executes first.

## Execution order

Middleware runs from outermost to innermost on the way in, then in reverse on the way out. The order defined in `app.py` is:

1.  `LoggingMiddleware` (`logging.py`) — outermost; measures total request duration
2.  `IPBlockingMiddleware` (`ip_blocking.py`) — rejects blacklisted IPs at the perimeter
3.  `CORSMiddleware` and `CORSPreflightMiddleware` — handles CORS headers and `OPTIONS` preflight
4.  `DatabaseMiddleware` (`database.py`) — attaches and cleans up a database session
5.  `SecurityHeadersMiddleware` (`security.py`) — adds security headers to every response, including `413` body-too-large responses
6.  `MaxBodySizeMiddleware` (defined in `app.py`) — enforces `max_request_body_size` by tracking actual bytes from `receive()`; uses larger caps for `/suite/sum/` and `/suite/stash/` uploads
7.  `AuthenticationMiddleware` (`authentication.py`) — validates tokens and populates `request.state.user`
8.  `RateLimitMiddleware` (`rate_limiting.py`) — innermost HTTP middleware; enforces per-user and per-IP buckets

## Components

### Authentication (`authentication.py`)

Validates bearer tokens and cookie sessions, and populates `request.state` for downstream dependencies.

| Export | Description |
|--------|-------------|
| `AuthenticationMiddleware` | ASGI middleware that extracts tokens, calls `auth.verify_token`, handles CSRF for cookie auth, and marks internal requests |
| `get_current_user(request)` | FastAPI dependency that returns `TokenInfo` or raises `401` (or `500` for system errors). Enforces `account_locked`, `deletion_status=frozen`, and `force_username_change` checks |
| `get_optional_user(request)` | Dependency that returns `TokenInfo` or `None` without raising |
| `get_token_info(token)` | Verifies a raw token string and returns `TokenInfo` when valid |

Authentication flow:

*   Internal requests are detected with `api.is_self_test_request()` (localhost plus `X-Plexichat-Internal-Secret` compared with `hmac.compare_digest`). Internal requests bypass rate limits but still validate bearer tokens when present.
*   Public endpoints (login, register, health, version, capabilities, and docs) skip token validation. The list is built from the configurable `api_prefix`, not a hardcoded value.
*   Admin paths and webhook-execute paths have separate handling.
*   Bearer tokens use the `Authorization: Bearer <token>` or `Authorization: Bot <token>` format. Multiple `Authorization` headers are rejected.
*   Cookie sessions fall back to the `plexichat_token` cookie. Mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) require the `X-Plexichat-Request: 1` header and are rejected when `Sec-Fetch-Site: cross-site` is present. Successful cookie authentication sets `request.state.cookie_auth` and rolls the cookie with an updated `Max-Age`.

Example:

```python
from fastapi import Depends
from src.api.middleware.authentication import get_current_user, TokenInfo

@router.get("/protected")
async def handler(current_user: TokenInfo = Depends(get_current_user)):
    user_id = current_user.user_id
    username = current_user.username
```

`TokenInfo` fields include `user_id`, `username`, `session_id`, `token_type`, `account_type`, `permissions`, `account_locked`, `force_username_change`, and `deletion_status`.

### Rate limiting (`rate_limiting.py`)

Integrates the `src.core.ratelimit` engine as ASGI middleware.

| Export | Description |
|--------|-------------|
| `create_rate_limit_middleware()` | Factory that returns a configured `RateLimitMiddleware` subclass |
| `get_user_info_from_request(request)` | Extracts `user_id`, `ip_address`, `is_bot`, `is_admin`, `is_internal`, `is_webhook` for bucket selection |
| `extract_ip(request)` | Returns the trusted-proxy-aware client IP using `src.utils.net.get_client_ip` |
| `RateLimitMiddleware` / `extract_route_info` | Re-exported from `src.core.ratelimit` |

Behavior:

*   Bypasses limits for internal requests (detected via `is_self_test_request`) and for requests carrying a valid `X-RateLimit-Bypass` header (verified with `hmac.compare_digest` against `rate_limiting.bypass_secret`).
*   Recognizes bot and admin users from `request.state.user` or `request.state.admin_id`.
*   Must run after authentication in the logical request flow so that per-user buckets and admin bypass work. In Starlette this means it is added *before* `AuthenticationMiddleware` so it executes *after* authentication.
*   Uses `X-Forwarded-For` through `get_client_ip` when the direct peer is a trusted proxy, rather than keying on the proxy IP.

### Error handling (`error_handling.py`)

Converts exceptions into consistent JSON error responses and adds CORS headers to error responses.

Error format:

```json
{
  "error": {
    "code": 404,
    "message": "Resource not found"
  }
}
```

| Export | Description |
|--------|-------------|
| `ErrorHandlingMiddleware` | Catches all downstream exceptions; maps them to status codes and JSON bodies. Does not send a second response if headers were already sent |
| `setup_exception_handlers(app)` | Registers FastAPI exception handlers for `HTTPException`, `StarletteHTTPException`, `RequestValidationError`, `JoinApiError`, and the catch-all `Exception` |
| `get_status_code_for_exception(exc)` | Maps exception class names (for example, `NotFoundError` to `404`) using `ERROR_MAPPINGS` |
| `format_error_response(code, message, debug)` | Formats the standard `{"error": {"code", "message"}}` body |

Details:

*   `ERROR_MAPPINGS` maps core exception suffixes such as `NotFoundError` to `404`, `ValidationError` to `400`, `RateLimitError` to `429`, and others. Matching uses exact name or suffix.
*   Messages are sanitized: `5xx` responses return `Internal server error` outside debug mode, and SQL keywords are stripped from `4xx` messages. Non-`src.*` exceptions that match a mapping by name receive a generic message to avoid leaking internals.
*   Validation errors return `400` (matching `docs/errors.md`).
*   CORS headers on errors reflect only origins explicitly listed in configuration; wildcard reflection is never allowed when credentials are enabled.

### Logging (`logging.py`)

Measures complete request duration from `receive` until the final `http.response.body` chunk is sent.

| Export | Description |
|--------|-------------|
| `LoggingMiddleware` | ASGI middleware that logs `METHOD path - status - duration - ip` and submits server-side telemetry for `/api/` paths |
| `SKIP_PATHS` | Paths excluded from logging: `/api/v1/health`, `/health`, `/favicon.ico` |

Behavior:

*   Uses `time.perf_counter()` and wraps `send()` to record timing on the final body chunk, not just when the response object is created.
*   Resolves the client IP with `src.utils.net.get_client_ip` (trusted-proxy aware), matching authentication and rate limiting.
*   Resets per-request database metrics at the start of the request and submits `response_time_ms`, `db_queries`, and `db_time_ms` to `src.core.telemetry` for `/api/` requests.
*   Logs `5xx` as errors, `4xx` as warnings, and `2xx` as info. Exceptions are logged with the exception class name only.

### Database (`database.py`)

Manages a database session for each HTTP request. The middleware attaches the session to the request and ensures it is closed after the response completes, even when downstream handlers raise.

### IP blocking (`ip_blocking.py`)

Checks the client IP against the blocklist at the perimeter. Because it is registered outermost, blacklisted peers are rejected before CORS, rate limiting, or database work runs.

### Security (`security.py`)

Adds security headers (for example, `Content-Security-Policy`, `X-Frame-Options`) to every response. It is registered outside `MaxBodySizeMiddleware` so that `413` responses also include these headers.

## Adding new middleware

1.  Create a new module in this directory that implements the ASGI interface `async def __call__(self, scope, receive, send)`.
2.  Register it in `src.api.app.create_app()` with `app.add_middleware(YourMiddleware)`. Remember that the last call is outermost.
3.  If the middleware needs configuration, read it through `utils.config` or `src.api.config.get_api_config()`.
4.  Document the middleware in this README.

## Related documentation

*   `../README.md` — API module overview and lifespan tasks
*   `../routes/README.md` — endpoint routing
*   `src.core.ratelimit` — rate limit engine and storage
