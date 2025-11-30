# Rate Limiting Module

Advanced rate limiting for PlexiChat API with multiple bucket types, algorithms, and storage backends.

## Features

- Multiple bucket types (global, user, IP, route, resource, webhook)
- IP-based rate limiting for unauthenticated users
- Multiple algorithms (token bucket, sliding window, fixed window, leaky bucket)
- Hourly and daily limits
- Bot and webhook multipliers
- Admin/internal bypass
- Standard rate limit headers
- Thread-safe in-memory storage
- Redis adapter interface (for distributed deployments)
- Route decorators for custom limits
- FastAPI middleware integration

## Setup

```python
from src.core import ratelimit

# Basic setup with defaults
ratelimit.setup()

# Custom setup
ratelimit.setup(
    bot_multiplier=1.5,           # Bots get 50% more requests
    webhook_multiplier=1.0,       # Webhooks use standard limits
    enable_global_limit=True,     # Enforce global rate limit
    bypass_check=lambda uid, admin, internal: admin,  # Admins bypass
)

# Custom IP limit for unauthenticated users
from src.core.ratelimit import RateLimitConfig, RateLimitAlgorithm, BucketType

custom_ip_limit = RateLimitConfig(
    requests=30,                   # 30 requests
    window_seconds=60.0,           # per minute
    burst=5,                       # with 5 burst
    algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
    scope=BucketType.IP,
    hourly_limit=900,              # 900 per hour
    daily_limit=5000,              # 5000 per day
)

ratelimit.setup(ip_config=custom_ip_limit)
```

## Usage

### Check Rate Limit

```python
from src.core import ratelimit

result = ratelimit.check_rate_limit(
    user_id=12345,
    route="POST /channels/{id}/messages",
    resource_id=channel_id,
    is_bot=False,
)

if not result.allowed:
    # Return 429 with headers
    headers = ratelimit.get_headers(result)
    return JSONResponse(
        status_code=429,
        content=result.response_body,
        headers=headers,
    )
```

### FastAPI Middleware

```python
from src.api import create_app
from src.core import ratelimit

# Setup rate limiting before creating app
ratelimit.setup()

# App automatically includes rate limit middleware
app = create_app()
```

### Route Decorators

```python
from fastapi import APIRouter, Request
from src.core.ratelimit import rate_limit, RateLimitAlgorithm

router = APIRouter()

@router.post("/messages")
@rate_limit(requests=5, window_seconds=5, burst=3)
async def send_message(request: Request):
    ...

@router.post("/upload")
@rate_limit(
    requests=2,
    window_seconds=60,
    algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    hourly_limit=20,
)
async def upload_file(request: Request):
    ...
```

## Configuration

### Default Route Limits

| Route                        | Limit | Window | Algorithm    |
| ---------------------------- | ----- | ------ | ------------ |
| POST /auth/login             | 5     | 60s    | Fixed Window |
| POST /auth/register          | 3     | 60s    | Fixed Window |
| POST /channels/{id}/messages | 5     | 5s     | Token Bucket |
| PATCH /users/@me             | 2     | 60s    | Fixed Window |
| PUT /reactions               | 1     | 0.25s  | Token Bucket |
| POST /webhooks/{id}/{token}  | 5     | 2s     | Token Bucket |

### Custom Route Configuration

```python
from src.core.ratelimit import RateLimitConfig, RateLimitAlgorithm, BucketType

custom_routes = {
    "POST /custom/endpoint": RateLimitConfig(
        requests=10,
        window_seconds=30,
        burst=5,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.USER,
        hourly_limit=100,
        daily_limit=1000,
    ),
}

ratelimit.setup(route_configs=custom_routes)
```

## Rate Limit Headers

All responses include standard rate limit headers:

```
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 4
X-RateLimit-Reset: 1699999999.123
X-RateLimit-Reset-After: 4.567
X-RateLimit-Bucket: abc123def456
X-RateLimit-Scope: channel
```

On 429 responses:

```
Retry-After: 5
X-RateLimit-Global: true  (if global limit hit)
```

## Algorithms

### Token Bucket (default for messaging)

- Tokens refill continuously
- Allows bursts up to bucket capacity
- Best for: APIs with variable traffic

### Sliding Window (default for most routes)

- Tracks individual request timestamps
- Smooth rate limiting without hard edges
- Best for: General API protection

### Fixed Window (default for auth)

- Simple counter per time window
- Resets at window boundary
- Best for: Login/registration protection

### Leaky Bucket

- Requests "leak" out at constant rate
- Smooths traffic spikes
- Best for: Downstream protection

## Bucket Types

- **Global**: Per-user across all requests (50/second default)
- **User**: Per-authenticated-user general limit (120/minute default)
- **IP**: Per-IP-address for unauthenticated users (60/minute default)
- **Route**: Per-user per-route
- **Resource**: Per-user per-resource (e.g., per channel)
- **Webhook**: Per-webhook
- **Channel Webhook**: Shared limit for all webhooks in a channel

### How IP-Based Limiting Works

For **authenticated requests** (user has a valid token):

- Rate limits are tracked per User ID
- Each user gets their own independent rate limit buckets
- Example: User A can send 120 requests/minute, User B can also send 120 requests/minute

For **unauthenticated requests** (no token, e.g., login/register):

- Rate limits are tracked per IP address
- Each IP gets its own independent rate limit bucket
- Example: IP 192.168.1.10 spamming login won't affect IP 192.168.1.20
- IP extracted from `X-Forwarded-For` header (for proxies) or direct connection

This prevents scenarios where:

- A malicious user spamming `/auth/login` blocks all other users from logging in
- Your Flask proxy server exhausts shared limits and blocks legitimate clients

## Bypass Configuration

```python
def custom_bypass(user_id, is_admin, is_internal):
    # Admins bypass all limits
    if is_admin:
        return True
    # Internal requests bypass
    if is_internal:
        return True
    # Specific users bypass
    if user_id in PREMIUM_USERS:
        return True
    return False

ratelimit.setup(bypass_check=custom_bypass)
```

## Storage Backends

### In-Memory (default)

```python
from src.core.ratelimit.storage import MemoryStorage

storage = MemoryStorage(
    cleanup_interval=60.0,  # Cleanup every 60 seconds
    max_buckets=100000,     # Maximum buckets to store
)
ratelimit.setup(storage_backend=storage)
```

### Redis (interface only)

```python
from src.core.ratelimit.storage import RateLimitStorage

class RedisStorage(RateLimitStorage):
    def __init__(self, redis_client):
        self.redis = redis_client

    def get_bucket(self, key):
        data = self.redis.get(f"ratelimit:{key}")
        return json.loads(data) if data else None

    # Implement other methods...
```

## Management

```python
# Reset specific bucket
ratelimit.reset_bucket("user:12345:route:abc123")

# Reset all buckets for a user
ratelimit.reset_user(12345)

# Reset all buckets
ratelimit.reset_all()

# Get bucket info
bucket = ratelimit.get_bucket_info("user:12345:route:abc123")
print(f"Remaining: {bucket.tokens}")
```

## Testing

```bash
pytest src/tests/ratelimit/ -v
```

## Error Response

429 responses include:

```json
{
  "message": "You are being rate limited.",
  "retry_after": 4.567,
  "global": false,
  "scope": "channel"
}
```
