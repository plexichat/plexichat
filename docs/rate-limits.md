# Rate limits

Plexichat applies multiple layers of request protection to keep the API stable under normal traffic and abuse conditions. All defaults in this document come from `src/config_defaults.py:273` and `src/core/ratelimit/config.py:13`.

## Default limits

Configure limits under `rate_limiting` in `config.yaml`. The server reloads them at startup. If the config key is missing, the fallback values in `src/core/ratelimit/config.py:13` apply.

### Global limits

Global limits apply to all requests regardless of source. They act as a backstop against total server overload.

*   **25000** requests per **60** seconds, burst **5000**

```yaml
rate_limiting:
  global:
    requests: 25000
    window_seconds: 60.0
    burst: 5000
```

### Per-user limits

Per-user limits apply to each authenticated user.

*   **10000** requests per **60** seconds, burst **2500**
*   **300000** requests per hour
*   **5000000** requests per day

```yaml
rate_limiting:
  user:
    requests: 10000
    window_seconds: 60.0
    burst: 2500
    hourly_limit: 300000
    daily_limit: 5000000
```

### Per-IP limits

Per-IP limits apply to each client IP address regardless of authentication. They catch unauthenticated abuse and credential-stuffing attempts.

*   **5000** requests per **60** seconds, burst **1500**
*   **150000** requests per hour
*   **1000000** requests per day

```yaml
rate_limiting:
  ip:
    requests: 5000
    window_seconds: 60.0
    burst: 1500
    hourly_limit: 150000
    daily_limit: 1000000
```

Configure `api.trusted_proxies` and `api.trust_x_forwarded_for` when you run behind a reverse proxy. Otherwise all traffic appears to come from the proxy IP and shares a single bucket. See [API and server configuration](deployment/configuration/config-api.md).

### Multipliers and bypass

```yaml
rate_limiting:
  bot_multiplier: 1.5
  webhook_multiplier: 1.0
  admin_bypass: true
  internal_bypass: true
```

| Field | Default | Description |
|---|---|---|
| `bot_multiplier` | `1.5` | Multiplier for bot accounts. A bot gets 15000 requests per minute when users get 10000. |
| `webhook_multiplier` | `1.0` | Multiplier for webhook delivery requests. |
| `admin_bypass` | `true` | When `true`, admins bypass per-user limits. Global and IP limits still apply. |
| `internal_bypass` | `true` | When `true`, internal service-to-service requests bypass limits. |

The server also supports a bypass secret in the `X-RateLimit-Bypass` header. The value is compared with `hmac.compare_digest` on UTF-8 encodings to avoid timing attacks and `500` errors on malformed headers. See `src/api/middleware/rate_limiting.py:102` and `src/core/ratelimit/middleware.py:250`.

Do not use stale values such as `bot_multiplier: 0.5` or `webhook_multiplier: 2.0`. A multiplier below `1.0` restricts bots below user levels, which you rarely want.

## Route-level limits

The code defines stricter limits for sensitive or high-volume routes in addition to the global, user, and IP limits. See `src/core/ratelimit/config.py:170` (`DEFAULT_ROUTE_LIMITS`).

Common examples:

*   Auth endpoints — `POST /auth/login` (5 per 60s), `POST /auth/register` (3 per 60s), `POST /auth/2fa/*` (5 per 60s). See `src/core/ratelimit/middleware.py:16`.
*   Message send — `POST /channels/{id}/messages` (10 per 5s, burst 5, wired to `rate_limiting.routes.send_message`).
*   Media uploads — `POST /media/upload` (10 per 60s, hourly 100).
*   Webhooks — `POST /webhooks/{id}/{token}` (5 per 2s).
*   Static client — `static_client:html` (300 per 60s) and `static_client:assets` (5000 per 60s), wired through `src/config_defaults.py:290`.

Use `get_route_config(route)` and `get_default_config()` from `src/core/ratelimit/config.py:662` to inspect the active route config.

## How the middleware works

Plexichat enforces limits in ASGI middleware (`src/core/ratelimit/middleware.py:170` and `src/api/middleware/rate_limiting.py:14`). For each HTTP request the middleware:

1.  Extracts the client IP with `src/utils/net.py:get_client_ip()` (trusted-proxy aware).
2.  Extracts user info (`user_id`, `is_bot`, `is_admin`, `is_internal`) from `request.state`.
3.  Resolves a route pattern with `extract_route_info()` (`src/core/ratelimit/middleware.py:139`).
4.  Calls `ratelimit.check_rate_limit()` and returns `429` with rate-limit headers when the check fails.

`OPTIONS` requests (CORS preflight) and paths in `exclude_paths` are not rate-limited.

## What clients should expect

*   Limits can be applied globally, per user, per IP, or per route and resource.
*   The server can tune any limit away from the code defaults in `src/config_defaults.py:273`.
*   Bots and feature tiers may receive different multipliers depending on server policy. See `src/core/ratelimit/config.py:76`.
*   Admin and internal bypass behavior is configurable. Do not assume it is available.

## Responses and headers

Rate-limited requests return `429 Too Many Requests` with standard rate-limit headers and retry metadata in the response body. See `src/core/ratelimit/middleware.py:290`.

On success, the middleware can include rate-limit headers when `include_headers_on_success` is `true` (the default in `src/api/middleware/rate_limiting.py:121`).

## Client recommendations

*   Back off on `429` instead of retrying immediately. Use exponential backoff.
*   Use pagination and caching to reduce repeated reads.
*   Batch operations where the API supports it.
*   Keep message send loops and reaction spam under control.
*   Prefer gateway events over high-frequency polling.

## Related pages

*   [Rate limiting configuration](deployment/configuration/config-rate-limiting.md)
*   [Messages](api/messages.md)
*   [Reactions](api/reactions.md)
*   [WebSocket close codes](websocket/close-codes.md)
