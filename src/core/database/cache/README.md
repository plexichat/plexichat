# Cache Module

Provides a two-tier caching layer using Valkey (Redis-compatible) as the primary store and an in-memory dictionary as a fallback when Valkey is unavailable.

## Overview

The cache module offers decorator-based and manual cache operations for frequently accessed data such as user profiles, server info, sessions, and presence. It automatically falls back to an in-memory L1 cache when the Valkey backend is unreachable, ensuring single-node deployments and degraded environments continue to function.

## Architecture

```
┌─────────────────────────────────────────┐
│            Caller / Service             │
├──────────────┬──────────────────────────┤
│  @cached     │  cache_get / cache_set   │
├──────────────┴──────────────────────────┤
│        Valkey (Primary Store)           │
│   ┌─────────────────────────────────┐   │
│   │  Key-Value (JSON serialized)    │   │
│   │  Sets (session tracking)        │   │
│   │  Lua scripts (rate limiting)    │   │
│   └─────────────────────────────────┘   │
├─────────────────────────────────────────┤
│     In-Memory Fallback (L1 Cache)       │
│   ┌─────────────────────────────────┐   │
│   │  Dict with TTL + LRU eviction   │   │
│   │  Thread-safe rate limiting       │   │
│   └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

The system uses a write-through strategy: data is written to both tiers. On reads, it checks Valkey first, then falls back to memory. The in-memory tier also serves as a warm cache after Valkey hits, so if Valkey goes down immediately after a hit, subsequent lookups still succeed from memory.

## Key Files

| File | Purpose |
|------|---------|
| `base.py` | In-memory cache storage, `CacheError` exception, TTL defaults |
| `decorators.py` | `@cached` decorator for sync/async functions |
| `operations.py` | Manual `cache_get`, `cache_set`, `cache_delete`, `cache_get_many`, `invalidate_pattern` |
| `session.py` | Session caching with user-to-session tracking sets |
| `presence.py` | User presence/status caching with bulk fetch support |
| `rate_limit.py` | Sliding-window rate limiting with atomic Lua INCR+EXPIRE |
| `monitoring.py` | Cache health checks, hit/miss/error statistics |
| `serialization.py` | JSON serialization, cache key generation, type-tagged reconstruction |

## Usage

### Decorator-based caching

```python
from src.core.database.cache import cached

@cached(ttl=300, prefix="user_profile")
async def get_user_profile(user_id: int) -> dict:
    return await db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
```

### Manual cache operations

```python
from src.core.database.cache import cache_get, cache_set, cache_delete, invalidate_pattern

# Set a value
cache_set("session:abc123", {"user_id": 42}, ttl=1800)

# Get a value
data = cache_get("session:abc123")

# Delete a value
cache_delete("session:abc123")

# Invalidate by glob pattern
invalidate_pattern("presence:*")
```

### Rate limiting

```python
from src.core.database.cache import check_rate_limit, reset_rate_limit

allowed, remaining = check_rate_limit(
    key="user:123:messages",
    limit=30,
    window_seconds=60,
)
if not allowed:
    raise HTTPException(429, "Rate limit exceeded")
```

### Cache monitoring

```python
from src.core.database.cache import cache_stats, cache_health, reset_cache_stats

stats = cache_stats()      # {"hits": 142, "misses": 8, "errors": 0}
health = cache_health()    # {"available": True, "hit_rate": 94.67, ...}
reset_cache_stats()        # Reset counters
```

## Configuration

Default TTLs are resolved from `valkey.ttl.<kind>` in the application config, falling back to:

| Kind | Default TTL |
|------|------------|
| `cache` | 60 seconds |
| `presence` | 300 seconds |
| `session` | 1800 seconds |

The in-memory tier caps at 1000 entries with LRU-style eviction when full.

Rate limiting fails closed by default on backend errors. Set `rate_limiting.fail_open_on_backend_error: true` in config to fail open.

## Related Code

- Valkey client: `src/core/database/valkey_client.py`
- `@cached` decorator usage: `src/core/relationships/manager/status.py:21`
- Session cache helpers: `src/core/database/cache/session.py:21`
- Presence cache: `src/core/database/cache/presence.py:15`
- Rate limit Lua script: `src/core/database/cache/rate_limit.py:28`
- Serialization type registry: `src/core/database/cache/serialization.py:17`
