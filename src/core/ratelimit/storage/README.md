# Rate limit storage

Store and manage rate limit bucket state for single-instance and distributed deployments.

## Overview

The storage module defines the `RateLimitStorage` abstract base class and its implementations. The rate limit manager depends on this interface, so you can swap backends without changing limit logic. The default backend is `MemoryStorage`, an in-process, thread-safe store.

## Files

| File | Description |
|------|-------------|
| `base.py` | Abstract base class `RateLimitStorage`. Defines the interface all backends must implement. |
| `memory.py` | Thread-safe in-memory backend for single-instance deployments. |

## Before you begin

Choose a backend based on your deployment:

* Use `MemoryStorage` for single-instance or development deployments.
* Implement `RateLimitStorage` on top of Valkey, Redis, or a database for multi-worker deployments.

## Implement the interface

All backends must implement `RateLimitStorage`:

| Method | Description |
|--------|-------------|
| `get_bucket(key)` | Get bucket state by key. Returns a dict or `None`. |
| `set_bucket(key, state, ttl)` | Set bucket state with an optional TTL in seconds. |
| `delete_bucket(key)` | Delete a bucket. Returns `True` if deleted. |
| `get_keys_by_prefix(prefix)` | List keys that start with `prefix`. |
| `delete_by_prefix(prefix)` | Delete keys by prefix. Returns the number deleted. |
| `clear_all()` | Clear all buckets. |
| `increment(key, field, amount)` | Atomically increment a field. Returns the new value. |
| `get_and_set(key, field, value, default)` | Atomically get and set a field. Returns the previous value. |
| `add_to_list(key, field, value, max_size)` | Append to a list field, trimming to `max_size`. Returns the new size. |
| `trim_list(key, field, min_value)` | Remove list items less than `min_value`. Returns the number removed. |
| `acquire_lock(key, timeout)` | Acquire a lock. Returns a token or `None`. |
| `release_lock(key, token)` | Release a lock. |
| `eval_token_bucket(key, capacity, refill_rate, cost, ttl)` | Atomically evaluate a token bucket. Returns `(allowed, remaining, reset_after)`. |
| `update_bucket(key, mutate_fn, ttl)` | Read-modify-write a bucket under a lock. Provided with a default implementation that uses `acquire_lock` / `release_lock`. |
| `close()` | Close the backend connection. Optional override. |

The default `update_bucket` implementation holds the distributed lock across `get_bucket` → `mutate` → `set_bucket`. `MemoryStorage` overrides it with a per-key lock for better concurrency.

## Use `MemoryStorage`

```python
from src.core.ratelimit.storage import MemoryStorage

storage = MemoryStorage(
    cleanup_interval=60.0,  # Seconds between expired-bucket cleanup
    max_buckets=100000,     # Maximum buckets before LRU eviction
)

storage.set_bucket("user:123:route:abc", {"tokens": 5, "last_update": 123456.0}, ttl=60.0)
state = storage.get_bucket("user:123:route:abc")
```

### Thread safety

`MemoryStorage` uses per-key locks for bucket operations and a global `RLock` for structural changes. This allows concurrent reads on different keys without process-wide contention. `get_bucket` returns a copy of the state so callers cannot mutate shared state outside the lock.

### Expiration and eviction

* Buckets with a TTL are removed by periodic cleanup (every `cleanup_interval` seconds) and on access if expired.
* When `max_buckets` is exceeded, the least recently updated buckets are evicted.
* Per-key lock objects are retained after bucket deletion to avoid race conditions where two callers create different lock instances for the same key. Orphan locks are pruned gradually when the lock pool exceeds `1.5 × max_buckets`.

### Atomic token bucket evaluation

```python
allowed, remaining, reset_after = storage.eval_token_bucket(
    key="user:123:route:abc",
    capacity=5,
    refill_rate=1.0,
    cost=1,
    ttl=86400,
)
```

This method is atomic under the per-key lock and uses copy-on-write semantics so concurrent `increment` calls do not clobber fields.

## Create a distributed backend

Extend `RateLimitStorage` and implement the abstract methods. The simplest path is to wrap a Valkey or Redis client:

```python
import json
from src.core.ratelimit.storage import RateLimitStorage

class ValkeyStorage(RateLimitStorage):
    def __init__(self, valkey_client):
        self.valkey = valkey_client

    def get_bucket(self, key):
        data = self.valkey.get(f"ratelimit:{key}")
        return json.loads(data) if data else None

    def set_bucket(self, key, state, ttl=None):
        data = json.dumps(state)
        if ttl is not None:
            self.valkey.setex(f"ratelimit:{key}", int(ttl), data)
        else:
            self.valkey.set(f"ratelimit:{key}", data)

    # Implement remaining methods: delete_bucket, get_keys_by_prefix,
    # delete_by_prefix, clear_all, increment, get_and_set, add_to_list,
    # trim_list, acquire_lock, release_lock, eval_token_bucket
```

For atomic operations such as `increment` and `eval_token_bucket`, use Lua scripts or transactions on the backing store.

## Pass a backend to the rate limiter

```python
from src.core import ratelimit
from src.core.ratelimit.storage import MemoryStorage

storage = MemoryStorage(cleanup_interval=60.0, max_buckets=100000)
ratelimit.setup(storage_backend=storage)

# For a distributed backend, pass your ValkeyStorage instance instead
# ratelimit.setup(storage_backend=ValkeyStorage(valkey_client))
```

## Inspect storage

```python
stats = storage.get_stats()
# {"bucket_count": 42, "ttl_count": 12, "lock_count": 50, "max_buckets": 100000}
```
