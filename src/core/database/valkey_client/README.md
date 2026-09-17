# Valkey client

Valkey/Redis client for PlexiChat built on `valkey-glide-sync` and `valkey-glide`. The package uses a mixin architecture so each Valkey command group lives in its own module while sharing a single `ValkeyClient` class.

## Overview

The previous monolithic `valkey_client.py` was split into focused mixins that inherit from a shared base. `ValkeyClient` in `composer.py:22` combines all mixins through multiple inheritance. Python's method resolution order (MRO) dispatches calls to the correct mixin at runtime.

The client is disabled by default. Set `valkey.enabled: true` in your config to enable it. The legacy `redis` config key is still accepted as an alias. See `base.py:67`.

## Two handles, two interfaces

GLIDE ships two packages over the same symbol surface, and the client keeps one handle for each. See the `ValkeyClientBase` docstring at `base.py:39`.

| Handle | Package | Field | Interface | Threading rule |
|--------|---------|-------|-----------|----------------|
| Blocking | `valkey-glide-sync` | `_client` | `get`, `set`, `eval_lua`, ... | Off the event loop only |
| Async | `valkey-glide` | `_aclient` | `aget`, `aset`, `aeval_lua`, ... | Must be awaited |

Both handles exist because a handle belongs to the loop (`glide`) or the thread (`glide_sync`) that created it. One handle cannot serve the serving loop and a worker thread at once, and sharing one async handle across loops raises on cross-loop use; two handles keeps every caller on the client it can actually use. The async handle is opened lazily, so a deployment that never touches an async path pays for one connection, not two.

**Choose the interface by caller, not by taste.** Anything on the event loop (ASGI routes, middleware, WebSocket handlers, gateway tasks) must use the `a*` methods; anything already off the loop (threadpool workers, CLI commands, the blocking startup path, sync tests) uses the blocking ones. A blocking call made from the loop stalls every other request on that worker.

### Async setup and teardown

The async handle must be opened **inside the serving loop**:

```python
from src.core.database.valkey_client import asetup

async def main():
    client = await asetup()      # None when Valkey is disabled or unreachable
    if client is not None:
        await client.aset("key", "value")
    await client.aclose()
```

`asetup()` is idempotent and degrades like `setup()`: it returns `None` instead of raising when Valkey is disabled or unreachable, so a missing cache never takes the server down.

The app wires this up for you. `_start_async_valkey()` runs on startup and `_stop_async_valkey()` on shutdown (`src/api/app.py`), so the async handle is opened and closed while the loop is still alive. `PlexichatServer.cleanup()` closes the *blocking* handle, off-loop, where a thread-bound handle belongs.

### Fail closed before setup

An `a*` call made before the async handle is open raises instead of returning a cache miss:

```python
client = ValkeyClient()
await client.aget("key")   # ValkeyConnectionError: call aconnect() first
```

That is deliberate. A silent `None` on an un-opened handle is indistinguishable from a genuine miss, which is how a rate limiter or a session lookup quietly degrades into a bypass. See `_ensure_aconnected` at `base.py:273`.

## Structure

```
valkey_client/
├── __init__.py    # Package entry point; re-exports ValkeyClient and helpers
├── base.py        # ValkeyClientBase: config, connection, key helpers, error types
├── basic.py       # BasicMixin: get, set, delete, exists, expire, ttl
├── json.py        # JSONMixin: json_get, json_set, json_delete, json_merge
├── hash.py        # HashMixin: hget, hset, hdel, hgetall, hmset
├── list.py        # ListMixin: lpush, rpush, lpop, rpop, lrange, llen, ltrim
├── set.py         # SetMixin: sadd, srem, smembers, sismember
├── counter.py     # CounterMixin: incr, decr
├── pubsub.py      # PubSubMixin: publish, subscribe, unsubscribe
├── lock.py        # LockMixin: acquire_lock, release_lock
├── admin.py       # AdminMixin: ping, health_check, flush_prefix, keys
├── composer.py    # ValkeyClient: composition of all mixins
└── README.md      # This file
```

Each mixin inherits from `ValkeyClientBase` and implements a focused set of commands, in both a blocking and an awaitable form. Cross-mixin calls (for example, `JSONMixin` calling `BasicMixin.set`) are declared as stubs on the base class so type checkers can resolve them.

### Async command surface

Every command group has an awaitable counterpart, named with an `a` prefix:

| Mixin | Async methods |
|-------|---------------|
| `basic.py` | `aset`, `aset_bin`, `aget`, `aget_bin`, `adelete`, `aexists`, `aexpire`, `attl` |
| `json.py` | `aset_json`, `aget_json`, `amget_json` |
| `hash.py` | `ahset`, `ahget`, `ahgetall`, `ahdel`, `ahmset` |
| `list.py` | `alpush`, `arpush`, `alpop`, `arpop`, `alrange`, `allen`, `altrim` |
| `set.py` | `asadd`, `asrem`, `asmembers`, `asismember` |
| `counter.py` | `aincr`, `adecr` |
| `pubsub.py` | `apublish`, `asubscribe`, `aunsubscribe`, `aget_pubsub_message`, `atry_get_pubsub_message` |
| `lock.py` | `aacquire_lock`, `arelease_lock` |
| `admin.py` | `aping`, `ahealth_check`, `aflush_prefix`, `akeys` |

The async methods return values in the same shapes as their blocking counterparts, so call sites differ only by `await`.

Error types defined in `base.py:24`:

* `ValkeyError` — Base class for all Valkey errors.
* `ValkeyConnectionError` — Connection failures.
* `ValkeyOperationError` — Operation failures when disabled or not connected.

## Prerequisites

* Valkey or Redis server reachable at the configured `host` and `port`.
* `valkey-glide-sync` Python package for the blocking interface: `pip install valkey-glide-sync`
* `valkey-glide` Python package for the async interface: `pip install valkey-glide`

Both are pinned in `requirements.in` (not redundancy: one package per handle, see the table above). If the package for the handle you are opening is missing, `connect()` / `aconnect()` raises `ImportError` with installation instructions.

## Usage

### Connect and use basic commands

```python
from src.core.database.valkey_client import ValkeyClient

client = ValkeyClient()
client.connect()

client.set("my_key", "my_value", ttl=300)
value = client.get("my_key")
client.delete("my_key")

client.close()
```

You can also use the client as a context manager, in either flavour:

```python
from src.core.database.valkey_client import ValkeyClient

with ValkeyClient() as client:
    client.set("key", "value")

async with ValkeyClient() as client:
    await client.aset("key", "value")
```

The blocking form opens/closes `_client`; the async form opens/closes `_aclient`. `aclose()` leaves the blocking handle untouched, so a mixed caller does not lose its connection.

### Use domain-specific mixins

All mixin methods are available directly on `ValkeyClient`:

```python
client = ValkeyClient()
client.connect()

# Hash commands
client.hset("user:1", "name", "Alice")
client.hget("user:1", "name")

# List commands
client.lpush("queue", "item")
client.lrange("queue", 0, 10)

# Pub/Sub
client.publish("channel", "message")

# Distributed lock
if client.acquire_lock("my_lock", ttl=30):
    try:
        # ... critical section ...
        pass
    finally:
        client.release_lock("my_lock")
```

### Module-level helpers

`__init__.py` exposes convenience functions that wrap a shared client instance:

```python
from src.core.database.valkey_client import (
    setup,
    asetup,
    get_client,
    is_available,
)

# Initialize the shared client from config (blocking, off-loop)
client = setup()

# Open the async handle on the shared client (inside the serving loop)
client = await asetup()

# Get the existing shared client
client = get_client()

# Check whether Valkey is available
if is_available():
    client.set("key", "value")

# From the event loop, use the awaitable interface instead
if get_client() is not None:
    await get_client().aset("key", "value")
```

`get_client()` returns the same object either way; `asetup()` only opens the second (async) handle on it. `is_available()` reports the *blocking* handle's state, so an event-loop caller that needs certainty about the async path should check the client it holds rather than this helper.

## Configuration

Configure the client in `config.json` under the `valkey` key:

```json
{
  "valkey": {
    "enabled": true,
    "host": "localhost",
    "port": 6379,
    "password": "",
    "db": 0,
    "ssl": false,
    "key_prefix": "plexichat:",
    "connection_pool": {
      "timeout": 5
    },
    "ttl": {
      "session": 1800,
      "presence": 300,
      "cache": 60
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Whether the client attempts to connect. |
| `host` | `localhost` | Valkey host. |
| `port` | `6379` | Valkey port. |
| `db` | `0` | Logical database index. |
| `key_prefix` | `plexichat:` | Prefix applied to all keys. |
| `ttl.session` | `1800` | Default TTL for session keys (seconds). |
| `ttl.presence` | `300` | Default TTL for presence keys (seconds). |
| `ttl.cache` | `60` | Default TTL for cache keys (seconds). |

## Key handling

* `_prefixed_key` (`base.py:283`) adds the configured prefix if it is not already present.
* `_sanitize_key` (`base.py:288`) strips non-printable characters and truncates keys to 512 characters.
* `eval_lua` / `aeval_lua` execute a Lua script with automatic key prefixing.

Prefixing and sanitising are shared by both interfaces, so a key written through `aset` is readable through `get` and vice versa. Mixing interfaces is safe for correctness — only the threading rule above limits where each one may be called.
