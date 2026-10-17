# Rate limit storage

Store and manage rate limit bucket state for single-instance and distributed deployments.

## Overview

The storage module defines the `RateLimitStorage` abstract base class and its implementations. The rate limit manager depends on this interface, so you can swap backends without changing limit logic. The default backend is `MemoryStorage`, an in-process, thread-safe store.

## Files

| File | Description |
|------|-------------|
| `base.py` | Abstract base class `RateLimitStorage`. Defines both interfaces all backends must satisfy. |
| `memory.py` | Thread-safe in-memory backend for single-instance deployments. |
| `valkey.py` | Distributed backend over the Valkey client, with native async. |
| `database.py` | Relational backend (`DatabaseStorage`) over SQLite or PostgreSQL. |

## Before you begin

Choose a backend based on your deployment:

* Use `MemoryStorage` for single-instance or development deployments.
* Use `ValkeyStorage` or `DatabaseStorage` for multi-worker deployments — both ship with the module.
* Implement `RateLimitStorage` yourself only for a backend none of those cover.

## Two interfaces, one backend

`RateLimitStorage` is dual: subclasses implement the **blocking** methods, and
the **awaitable** `a*` methods are the interface the event loop uses (the ASGI
middleware, the route decorators, and any async handler).

| Blocking | Awaitable |
|----------|-----------|
| `get_bucket` | `aget_bucket` |
| `set_bucket` | `aset_bucket` |
| `delete_bucket` | `adelete_bucket` |
| `get_keys_by_prefix` | `aget_keys_by_prefix` |
| `delete_by_prefix` | `adelete_by_prefix` |
| `clear_all` | `aclear_all` |
| `increment` | `aincrement` |
| `get_and_set` | `aget_and_set` |
| `add_to_list` | `aadd_to_list` |
| `trim_list` | `atrim_list` |
| `acquire_lock` | `aacquire_lock` |
| `release_lock` | `arelease_lock` |
| `eval_token_bucket` | `aeval_token_bucket` |
| `update_bucket` | `aupdate_bucket` |
| `close` | `aclose` |

The default `a*` implementation offloads the matching blocking method with
`asyncio.to_thread`, which is correct for any backend without extra code.
Backends override it when they can do better:

* `MemoryStorage` calls straight through. Its state is an in-process dict, so
there is no I/O to get out of the way and a thread hop would be pure
overhead on the per-request hot path. Calling through also keeps
`acquire_lock` / `release_lock` on one thread, which matters because a
`threading.Lock` must be released by the thread that took it.
* `ValkeyStorage` drives its native async client (`aget_json`,
`aeval_lua`, ...), so a check never parks a pool thread on a network round
trip.
* `DatabaseStorage` keeps the `to_thread` default, since its backend is
blocking SQL.

### Composite operations must stay whole

`update_bucket` is a read-modify-write whose lock lives *inside* the blocking
call. An override must therefore offload the whole operation, never re-express
it as separate `aget_bucket` → mutate → `aset_bucket` awaits: splitting it
reintroduces exactly the lost-update race the method exists to prevent, and a
limit of 5 can admit every parallel request. `src/tests/ratelimit/test_async_path.py`
proves this holds under concurrent load.

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

### Lock contention fails closed

A backend that cannot take the distributed lock must not write anyway. `ValkeyStorage` returns the method's default (for `aincrement`, `0`) instead of
mutating an unlocked bucket, matching its blocking `increment`. Treat a
returned default as "not counted" rather than "counted as zero".

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

## Use a shipped distributed backend

```python
from src.core import ratelimit
from src.core.ratelimit.storage import DatabaseStorage, ValkeyStorage

ratelimit.setup(storage_backend=ValkeyStorage())   # Valkey / Redis
ratelimit.setup(storage_backend=DatabaseStorage())  # SQLite or PostgreSQL
```

Both implement the abstract methods plus an internal lock so composite
operations are atomic across workers, and both bound their bucket table so
crafted distinct keys cannot grow it without limit.

## Create your own backend

Extend `RateLimitStorage` and implement the abstract methods. The blocking
methods are the ones you must write; the `a*` forms come for free via the
`asyncio.to_thread` default unless you have a native async client worth
driving directly.

```python
import json
from src.core.ratelimit.storage import RateLimitStorage

class MyStorage(RateLimitStorage):
    def get_bucket(self, key):
        data = self.client.get(f"ratelimit:{key}")
        return json.loads(data) if data else None

    def set_bucket(self, key, state, ttl=None):
        self.client.set(f"ratelimit:{key}", json.dumps(state))

    # Implement remaining methods: delete_bucket, get_keys_by_prefix,
    # delete_by_prefix, clear_all, increment, get_and_set, add_to_list,
    # trim_list, acquire_lock, release_lock, eval_token_bucket
```

For atomic operations such as `increment` and `eval_token_bucket`, use Lua
scripts, transactions, or a lock on the backing store. Keep every composite
read-modify-write inside one method call — see "Composite operations must
stay whole" above.

## Pass a backend to the rate limiter

```python
from src.core import ratelimit
from src.core.ratelimit.storage import MemoryStorage

storage = MemoryStorage(cleanup_interval=60.0, max_buckets=100000)
ratelimit.setup(storage_backend=storage)

# For a distributed backend, pass a shipped backend instead
# ratelimit.setup(storage_backend=ValkeyStorage())
# ratelimit.setup(storage_backend=DatabaseStorage())
```

## Inspect storage

```python
stats = storage.get_stats()
# {"bucket_count": 42, "ttl_count": 12, "lock_count": 50, "max_buckets": 100000}
```
