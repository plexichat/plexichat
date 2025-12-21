# Rate Limits

PlexiChat uses rate limiting to ensure fair usage and protect the API from abuse.

## Rate Limit Algorithms

| Algorithm | Description |
|-----------|-------------|
| Token Bucket | Allows bursts, tokens refill over time |
| Sliding Window | Smooth rate limiting over rolling window |
| Fixed Window | Simple count per fixed time window |

## Global Limits

| Scope | Requests | Window | Burst | Algorithm |
|-------|----------|--------|-------|-----------|
| Per User | 120 | 60s | 20 | Sliding Window |
| Per Second | 50 | 1s | 10 | Token Bucket |

## Endpoint Limits

### Authentication

| Endpoint | Requests | Window | Hourly | Daily |
|----------|----------|--------|--------|-------|
| POST /auth/login | 5 | 60s | 20 | - |
| POST /auth/register | 3 | 60s | 10 | 20 |
| POST /auth/2fa | 5 | 60s | - | - |

### Messages

| Endpoint | Requests | Window | Burst |
|----------|----------|--------|-------|
| POST /channels/{id}/messages | 5 | 5s | 3 |
| PATCH /channels/{id}/messages/{id} | 5 | 5s | 2 |
| DELETE /channels/{id}/messages/{id} | 5 | 5s | 2 |
| GET /channels/{id}/messages | 10 | 10s | 5 |

### Reactions

| Endpoint | Requests | Window | Burst |
|----------|----------|--------|-------|
| PUT reactions | 1 | 0.25s | 1 |
| DELETE reactions | 1 | 0.25s | 1 |

### Users

| Endpoint | Requests | Window | Hourly |
|----------|----------|--------|--------|
| PATCH /users/@me | 2 | 60s | 10 |
| GET /users/@me | 30 | 60s | - |

### Servers

| Endpoint | Requests | Window | Daily |
|----------|----------|--------|-------|
| POST /servers | 10 | 60s | 100 |
| DELETE /servers/{id} | 1 | 60s | - |
| GET /servers/{id} | 20 | 10s | - |

### Relationships

| Endpoint | Requests | Window | Hourly |
|----------|----------|--------|--------|
| POST /relationships | 5 | 60s | 50 |
| POST /relationships/block | 10 | 60s | - |

### Webhooks

| Endpoint | Requests | Window | Burst |
|----------|----------|--------|-------|
| POST /webhooks | 5 | 60s | 2 |
| POST /webhooks/{id}/{token} | 5 | 2s | 5 |

## Rate Limit Headers

All responses include rate limit information:

```
X-RateLimit-Limit: 50
X-RateLimit-Remaining: 49
X-RateLimit-Reset: 1704067200
X-RateLimit-Bucket: route:POST:/channels/{id}/messages
```

| Header | Description |
|--------|-------------|
| X-RateLimit-Limit | Maximum requests in window |
| X-RateLimit-Remaining | Remaining requests |
| X-RateLimit-Reset | Unix timestamp when limit resets |
| X-RateLimit-Bucket | Bucket identifier |

## Rate Limited Response

When rate limited, you receive HTTP 429:

```json
{
  "error": {
    "code": 429,
    "message": "Rate limited",
    "retry_after": 1.5
  }
}
```

| Field | Description |
|-------|-------------|
| retry_after | Seconds to wait before retrying |

## Bot Rate Limits

Bots receive a 1.2x multiplier on high-traffic routes:

- POST /channels/{id}/messages
- GET /channels/{id}/messages
- PUT/DELETE reactions

## Bypassing Rate Limits

### Internal Requests

Internal services can bypass rate limits:

```
X-Internal-Request: true
```

### Admin Users

Users with admin permissions (`admin.*` or `*`) are exempt from rate limits.

## Best Practices

1. **Respect rate limits** - Don't retry immediately after 429
2. **Use exponential backoff** - Increase delay between retries
3. **Cache responses** - Reduce unnecessary requests
4. **Batch operations** - Combine multiple operations when possible
5. **Monitor headers** - Track remaining requests proactively

## WebSocket Rate Limits

WebSocket connections have separate limits:

| Scope | Events | Window |
|-------|--------|--------|
| Per Connection | 120 | 60s |

Exceeding WebSocket rate limits results in close code 4008 (RATE_LIMITED).
