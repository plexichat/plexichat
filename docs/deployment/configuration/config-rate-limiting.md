# Rate limiting configuration

This guide describes how to configure rate limiting for Plexichat. Rate limiting protects the server from abuse, ensures fair resource allocation, and keeps the service available under load. All defaults in this guide come from `src/config_defaults.py:273`.

## Before you begin

You configure rate limiting under the `rate_limiting` key in `config.yaml`. The server reloads this configuration at startup.

```yaml
rate_limiting:
  enabled: true
  global:
    requests: 25000
    window_seconds: 60.0
    burst: 5000
```

## Enable rate limiting

```yaml
rate_limiting:
  enabled: true
```

Set `enabled` to `true` in production. When you set it to `false`, the server disables all API and WebSocket rate limiting. Disable rate limiting only for local development and testing.

## Global limits

Global limits apply to all requests regardless of source. They act as a backstop against total server overload.

```yaml
rate_limiting:
  global:
    requests: 25000
    window_seconds: 60.0
    burst: 5000
```

| Field | Default | Description |
|---|---|---|
| `requests` | `25000` | Maximum requests allowed in the window. |
| `window_seconds` | `60.0` | Length of the window in seconds. |
| `burst` | `5000` | Maximum requests allowed in a single burst. Burst capacity refills at the normal rate. |

Do not use the stale values `100` requests and `50` burst from older documentation.

Tune global limits for your deployment size:

*   **Small server (up to 500 concurrent users):** The default `25000/60s` with `5000` burst handles typical chat traffic.
*   **High-traffic server (thousands of users):** Increase proportionally if you see frequent `429` responses under normal load.
*   **Resource-constrained server:** Reduce limits only after you confirm the server cannot handle the default throughput.

## Per-user limits

Per-user limits apply to each authenticated user. This tier handles most normal API usage.

```yaml
rate_limiting:
  user:
    requests: 10000
    window_seconds: 60.0
    burst: 2500
    hourly_limit: 300000
    daily_limit: 5000000
```

| Field | Default | Description |
|---|---|---|
| `requests` | `10000` | Requests per minute per user. |
| `window_seconds` | `60.0` | Window length in seconds. |
| `burst` | `2500` | Burst capacity per user. |
| `hourly_limit` | `300000` | Maximum requests per hour per user. |
| `daily_limit` | `5000000` | Maximum requests per day per user. |

The `hourly_limit` and `daily_limit` provide secondary ceilings. A user can hit these limits even if they stay under the per-minute rate.

Do not use the stale values `120` requests, `20` burst, `3600` hourly, or `50000` daily from older documentation.

### Burst and rate

With `requests: 10000` and `burst: 2500`, a user can send 2500 requests immediately, then sends at the refill rate of about 166 requests per second. Over a full minute, the user can send up to 10000 requests. A burst that is too low causes legitimate page loads to fail because loading a channel triggers several API calls at once. A burst that is too high allows short spikes of abuse.

### When to adjust

*   **Standard deployment:** Keep the default. It is generous for chat, browsing, and moderate bot use.
*   **Bot-heavy communities:** Increase to `12000`–`15000` per minute if users run multiple bots that aggregate traffic under one account.
*   **Public-facing servers with abuse concerns:** Decrease to `6000`–`8000` per minute.

## Per-IP limits

Per-IP limits apply to each client IP address regardless of authentication. They catch unauthenticated abuse and credential-stuffing attempts.

```yaml
rate_limiting:
  ip:
    requests: 5000
    window_seconds: 60.0
    burst: 1500
    hourly_limit: 150000
    daily_limit: 1000000
```

| Field | Default | Description |
|---|---|---|
| `requests` | `5000` | Requests per minute per IP. |
| `window_seconds` | `60.0` | Window length in seconds. |
| `burst` | `1500` | Burst capacity per IP. |
| `hourly_limit` | `150000` | Maximum requests per hour per IP. |
| `daily_limit` | `1000000` | Maximum requests per day per IP. |

Do not use the stale values `60` requests, `10` burst, `1800` hourly, or `10000` daily from older documentation.

Per-IP limits are stricter than per-user limits because a single IP should not generate high unauthenticated traffic.

If you run Plexichat behind a reverse proxy, configure `api.trusted_proxies` and `api.trust_x_forwarded_for`. Otherwise all traffic appears to come from the proxy IP and legitimate users share a single rate-limit bucket. See [API and server configuration](config-api.md).

If many users share one IP through NAT, such as in a corporate or educational network, consider increasing the per-IP limit to `10000`–`12000` per minute.

## Multipliers and bypass

### Bot and webhook multipliers

```yaml
rate_limiting:
  bot_multiplier: 1.5
  webhook_multiplier: 1.0
  admin_bypass: true
  internal_bypass: true
```

| Field | Default | Description |
|---|---|---|
| `bot_multiplier` | `1.5` | Multiplier applied to the per-user limit for bot accounts. At `1.5`, a bot gets `15000` requests per minute when users get `10000`. |
| `webhook_multiplier` | `1.0` | Multiplier for webhook delivery requests. |
| `admin_bypass` | `true` | When `true`, users with admin permissions bypass per-user limits. Global and IP limits still apply. |
| `internal_bypass` | `true` | When `true`, internal service-to-service requests bypass rate limiting. |

Do not use the stale values `bot_multiplier: 0.5` or `webhook_multiplier: 2.0` from older documentation. A multiplier below `1.0` restricts bots below user levels, which you rarely want.

*   To restrict bots to the same limit as users, set `bot_multiplier` to `1.0`.
*   For communities with many active bots, set `bot_multiplier` to `2.0`–`3.0`.
*   For high webhook volume, increase `webhook_multiplier` to `1.5`–`2.0`.
*   Keep `admin_bypass` enabled unless you need to enforce limits on admins for compliance.
*   Keep `internal_bypass` enabled unless you are testing rate-limiting behavior on internal requests.

The server also supports a bypass secret in the `X-RateLimit-Bypass` header. The default is auto-generated. Do not set it to a predictable value and do not expose it to clients or frontend code.

## Route-specific limits

Route-specific limits apply to the static client handler when you serve the web client from the server.

```yaml
rate_limiting:
  routes:
    static_client_html:
      requests: 300
      window_seconds: 60.0
      burst: 100
      hourly_limit: 6000
    static_client_assets:
      requests: 5000
      window_seconds: 60.0
      burst: 1000
      hourly_limit: 180000
```

These limits protect HTML and asset routes without affecting API traffic.

## Complete example

```yaml
rate_limiting:
  enabled: true
  global:
    requests: 25000
    window_seconds: 60.0
    burst: 5000
  user:
    requests: 10000
    window_seconds: 60.0
    burst: 2500
    hourly_limit: 300000
    daily_limit: 5000000
  ip:
    requests: 5000
    window_seconds: 60.0
    burst: 1500
    hourly_limit: 150000
    daily_limit: 1000000
  routes:
    static_client_html:
      requests: 300
      window_seconds: 60.0
      burst: 100
      hourly_limit: 6000
    static_client_assets:
      requests: 5000
      window_seconds: 60.0
      burst: 1000
      hourly_limit: 180000
  bot_multiplier: 1.5
  webhook_multiplier: 1.0
  admin_bypass: true
  internal_bypass: true
```

## Related documentation

*   [Default configuration reference](../../default-config.md) — Complete configuration reference with values from `src/config_defaults.py`.
*   [API and server configuration](config-api.md) — Trusted proxies, CORS, and debug mode.
*   [WebSocket configuration](config-websocket.md) — WebSocket-specific limits.
*   [Rate limits](../../rate-limits.md) — Dynamic rate limit reference.
