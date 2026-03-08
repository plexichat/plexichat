# Rate Limits

PlexiChat applies multiple layers of request protection to keep the API stable under normal traffic and abuse conditions.

## Default Buckets In Code

The backend defines these default baseline limits in `src/core/ratelimit/config.py`:

| Scope | Default |
|------|---------|
| global | 50 requests per 1 second, burst 10 |
| per-user | 70 requests per 60 seconds, burst 20 |
| per-IP | 70 requests per 60 seconds, burst 10 |

Servers can override these values through configuration.

## Route-Level Examples

The code also defines stricter limits for sensitive or high-volume routes, including:

| Route pattern | Default |
|--------------|---------|
| `POST /auth/login` | 5 per 60 seconds |
| `POST /auth/register` | 3 per 60 seconds |
| `POST /auth/2fa` | 5 per 60 seconds |
| `POST /channels/{id}/messages` | 5 per 5 seconds |
| `PATCH /channels/{id}/messages/{msg_id}` | 5 per 5 seconds |
| `DELETE /channels/{id}/messages/{msg_id}` | 5 per 5 seconds |
| reaction add/remove routes | 1 per 0.25 seconds |

## What Clients Should Expect

- limits may be applied globally, per user, per IP, or per route/resource
- a server can tune limits away from the code defaults
- bots and feature tiers may receive different multipliers depending on server policy
- admin and internal bypass behavior is configurable and should not be assumed by public clients

## Typical Response Signals

Rate-limited requests return `429 Too Many Requests` and may include standard rate-limit headers plus retry metadata in the error response.

## Client Recommendations

- back off on `429` instead of retrying immediately
- use pagination and caching to reduce repeated reads
- batch operations where supported
- keep message send loops and reaction spam under control
- prefer gateway events over high-frequency polling

## Related Pages

- [Messages](api/messages.md)
- [Reactions](api/reactions.md)
- [WebSocket Close Codes](websocket/close-codes.md)
