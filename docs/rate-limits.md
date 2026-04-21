# Rate Limits

Plexichat applies multiple layers of request protection to keep the API stable under normal traffic and abuse conditions.

## Default Limits

The server defines these baseline limits in the `rate_limiting` config section:

**Global limits** (applied to the entire server):
- 100 requests per 60 seconds, burst of 50

**Per-user limits** (applied per authenticated user):
- 120 requests per 60 seconds, burst of 20
- 3600 requests per hour
- 50000 requests per day

**Per-IP limits** (applied per client IP):
- 60 requests per 60 seconds, burst of 10
- 1800 requests per hour
- 10000 requests per day

**Multipliers**:
- Bot multiplier: 1.5x (bots get 1.5x user limits, not 0.5x -- this ensures bots can process events for all their subscribers)
- Webhook multiplier: 1.0x (webhooks get standard limits)

**Bypass options**:
- Admin bypass: enabled (admin users skip rate limits)
- Internal bypass: enabled (internal services skip rate limits via bypass secret)

Servers can override these values through configuration. See [Rate Limiting Configuration](config-rate-limiting.md) for details.

## Route-Level Limits

The code also defines stricter limits for sensitive or high-volume routes. These are enforced in addition to the global/user/IP limits above. Common examples:

- Auth endpoints (login, register): stricter per-IP and per-user limits
- Media uploads: limited by `media.rate_limit` settings
- Admin panel: separate rate limiting under `admin_ui.rate_limit`
- WebSocket gateway: `websocket.rate_limit_per_minute: 120`

## What Clients Should Expect

- Limits may be applied globally, per user, per IP, or per route/resource
- A server can tune limits away from the code defaults
- Bots and feature tiers may receive different multipliers depending on server policy
- Admin and internal bypass behavior is configurable and should not be assumed by public clients

## Typical Response Signals

Rate-limited requests return `429 Too Many Requests` and may include standard rate-limit headers plus retry metadata in the error response.

## Client Recommendations

- Back off on `429` instead of retrying immediately
- Use pagination and caching to reduce repeated reads
- Batch operations where supported
- Keep message send loops and reaction spam under control
- Prefer gateway events over high-frequency polling

## Related Pages

- [Rate Limiting Configuration](config-rate-limiting.md)
- [Messages](api/messages.md)
- [Reactions](api/reactions.md)
- [WebSocket Close Codes](websocket/close-codes.md)
