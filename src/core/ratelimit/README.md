# Rate limiting

Protect the Plexichat API with configurable rate limiting across multiple bucket types, algorithms, and storage backends.

## Overview

The rate limiting module enforces per-user, per-IP, per-route, and per-resource limits using algorithms such as token bucket, sliding window, fixed window, and leaky bucket. It supports in-memory storage for single-instance deployments and a `RateLimitStorage` interface for distributed backends such as Valkey.

You configure the module once at startup with `ratelimit.setup()` and then check limits in middleware or route handlers.

## Features

* Multiple bucket types: global, user, IP, route, resource, and webhook
* IP-based limiting for unauthenticated requests
* Four algorithms: token bucket, sliding window, fixed window, leaky bucket
* Hourly and daily limits with bot and webhook multipliers
* Admin and internal bypass
* Standard `X-RateLimit-*` and `Retry-After` headers
* Dual sync/async interfaces over one backend (blocking for off-loop callers, `a*` for the event loop)
* Three storage backends: in-memory, Valkey, and relational database
* Route decorators and FastAPI middleware, both on the async path

## Before you begin

Decide whether you need single-instance or distributed limiting. The default `MemoryStorage` is per-process; for multi-worker deployments, pass a shipped distributed backend (`ValkeyStorage` or `DatabaseStorage`) to `setup()`.

## Set up the module

### Basic setup

```python
from src.core import ratelimit

ratelimit.setup()
```

### Custom setup

```python
from src.core import ratelimit

ratelimit.setup(
    bot_multiplier=1.5,         # Bots get 50% more requests
    webhook_multiplier=1.0,     # Webhooks use standard limits
    enable_global_limit=True,   # Enforce the global bucket
    bypass_check=lambda uid, admin, internal: admin,  # Admins bypass
)
```

### Custom IP limit for unauthenticated users

```python
from src.core.ratelimit import RateLimitConfig, RateLimitAlgorithm, BucketType

custom_ip_limit = RateLimitConfig(
    requests=30,
    window_seconds=60.0,
    burst=5,
    algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
    scope=BucketType.IP,
    hourly_limit=900,
    daily_limit=5000,
)

ratelimit.setup(ip_config=custom_ip_limit)
```

If a distributed backend is already configured, calling `setup()` with `MemoryStorage` does not downgrade it. The call is refused and a warning is logged.

## Check a rate limit

```python
from src.core import ratelimit

result = ratelimit.check_rate_limit(
    user_id=12345,
    route="POST /channels/{id}/messages",
    resource_id=channel_id,
    is_bot=False,
)

if not result.allowed:
    headers = ratelimit.get_headers(result)
    return JSONResponse(
        status_code=429,
        content=result.response_body,
        headers=headers,
    )
```

`check_rate_limit` also accepts `ip_address`, `is_webhook`, `is_admin`, `is_internal`, `webhook_id`, and `cost`.

## Check a rate limit from the event loop

Every blocking entry point has an awaitable counterpart, prefixed with `a`.
On the event loop, use those:

```python
from src.core import ratelimit

result = await ratelimit.acheck_rate_limit(
    user_id=12345,
    route="POST /channels/{id}/messages",
    resource_id=channel_id,
    is_bot=False,
)

if not result.allowed:
    return JSONResponse(
        status_code=429,
        content=result.response_body,
        headers=ratelimit.get_headers(result),
    )
```

| Blocking | Awaitable |
|----------|-----------|
| `check_rate_limit` | `acheck_rate_limit` |
| `increment_custom_usage` | `aincrement_custom_usage` |
| `get_bucket_info` | `aget_bucket_info` |
| `reset_bucket` | `areset_bucket` |
| `reset_user` | `areset_user` |
| `reset_all` | `areset_all` |

The two paths share one implementation of the algorithms, key generation,
secondary limits, and result assembly, so they cannot report different
answers; only the storage round trips differ. Both interfaces are atomic
under parallel requests: a composite read-modify-write is sent to the
backend as a single locked operation, never as separate awaits that would
let two callers read the same snapshot and both be admitted.

**Which to call.** Anything running on the event loop — ASGI routes and
middleware, WebSocket handlers, gateway tasks — must use the `a*` forms. The
ASGI middleware and the `@rate_limit` decorator already do this for you;
the blocking forms are for callers already off the loop (threadpool workers,
CLI commands, migrations).

## Use FastAPI middleware

```python
from src.api import create_app
from src.core import ratelimit

ratelimit.setup()
app = create_app()  # App includes rate limit middleware automatically
```

## Use route decorators

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

### Default route limits

| Route | Limit | Window | Algorithm |
|-------|-------|--------|-----------|
| `POST /auth/login` | 5 | 60s | Fixed window |
| `POST /auth/register` | 3 | 60s | Fixed window |
| `POST /channels/{id}/messages` | 5 | 5s | Token bucket |
| `PATCH /users/@me` | 2 | 60s | Fixed window |
| `PUT /reactions` | 1 | 0.25s | Token bucket |
| `POST /webhooks/{id}/{token}` | 5 | 2s | Token bucket |

### Custom route configuration

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

Custom routes are merged with the defaults.

## Headers

Successful and rate-limited responses include:

```
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 4
X-RateLimit-Reset: 1699999999.123
X-RateLimit-Reset-After: 4.567
X-RateLimit-Bucket: abc123def456
X-RateLimit-Scope: channel
```

`429` responses add:

```
Retry-After: 5
X-RateLimit-Global: true   # Only when the global bucket is hit
```

## Algorithms

### Token bucket

Tokens refill continuously and allow bursts up to capacity. Best for APIs with variable traffic. Default for messaging routes.

### Sliding window

Tracks individual request timestamps for smooth limiting without hard edges. Best for general API protection. Default for most routes.

### Fixed window

A counter per time window that resets at the boundary. Best for login and registration protection. Default for auth routes.

### Leaky bucket

Requests leak at a constant rate to smooth traffic spikes. Best for protecting downstream services.

## Bucket types

| Type | Scope | Default |
|------|-------|---------|
| Global | Per-user across all requests | 50/second |
| User | Per authenticated user | 120/minute |
| IP | Per IP address (unauthenticated) | 60/minute |
| Route | Per user per route | Varies |
| Resource | Per user per resource (for example, per channel) | Varies |
| Webhook | Per webhook | Varies |
| Channel webhook | Shared limit for all webhooks in a channel | Varies |

### How IP-based limiting works

* **Authenticated requests** (valid token): Limits are tracked per user ID. Each user gets independent buckets.
* **Unauthenticated requests** (no token, such as login or registration): Limits are tracked per IP address extracted from `X-Forwarded-For` or the direct connection. This prevents one IP from exhausting a shared bucket for all users.

## Bypass configuration

```python
def custom_bypass(user_id, is_admin, is_internal):
    if is_admin:
        return True
    if is_internal:
        return True
    if user_id in PREMIUM_USERS:
        return True
    return False

ratelimit.setup(bypass_check=custom_bypass)
```

You can also call `ratelimit.set_bypass_check()` after setup.

## Storage backends

### In-memory (default)

Single-instance, thread-safe storage:

```python
from src.core.ratelimit.storage import MemoryStorage

storage = MemoryStorage(
    cleanup_interval=60.0,
    max_buckets=100000,
)
ratelimit.setup(storage_backend=storage)
```

### Valkey (distributed)

A Valkey-backed backend ships with the module — you do not need to write one:

```python
from src.core import ratelimit
from src.core.ratelimit.storage import ValkeyStorage

ratelimit.setup(storage_backend=ValkeyStorage())
```

`ValkeyStorage` drives the async GLIDE client for its `a*` methods and the
blocking client for the sync ones, so an event-loop check costs no thread
offload. Two properties matter in production:

* **Atomicity.** Buckets are mutated through a single Lua script or under a
distributed lock held across the whole get → mutate → set, so two workers
cannot lose each other's update.
* **Fail closed.** If Valkey is unreachable, checks deny (`allowed=False`)
rather than silently admitting every request. Deployments that intentionally
run without Valkey should select `MemoryStorage` explicitly in `setup()`.

When `setup()` already has a distributed backend, passing `MemoryStorage` is
refused with a warning instead of downgrading it. See `storage/README.md`
for the storage interface.

## Manage buckets

```python
# Reset a specific bucket
ratelimit.reset_bucket("user:12345:route:abc123")

# Reset all buckets for a user
ratelimit.reset_user(12345)

# Reset all buckets
ratelimit.reset_all()

# Inspect a bucket
bucket = ratelimit.get_bucket_info("user:12345:route:abc123")
print(f"Remaining: {bucket.tokens}")
```

## Test

```bash
pytest src/tests/ratelimit/ -v
```

`src/tests/ratelimit/test_async_path.py` covers the async interface: parity
with the blocking path for all four algorithms, the module-level `a*` API,
the native async storages, and atomicity under parallel requests.

## Error response

`429` responses include a JSON body:

```json
{
  "message": "You are being rate limited.",
  "retry_after": 4.567,
  "global": false,
  "scope": "channel"
}
```
