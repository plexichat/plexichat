# Valkey client

Valkey/Redis client for PlexiChat built on `valkey-glide-sync`. The package uses a mixin architecture so each Valkey command group lives in its own module while sharing a single `ValkeyClient` class.

## Overview

The previous monolithic `valkey_client.py` was split into focused mixins that inherit from a shared base. `ValkeyClient` in `composer.py:18` combines all mixins through multiple inheritance. Python's method resolution order (MRO) dispatches calls to the correct mixin at runtime.

The client is disabled by default. Set `valkey.enabled: true` in your config to enable it. The legacy `redis` config key is still accepted as an alias. See `base.py:42`.

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

Each mixin inherits from `ValkeyClientBase` (`base.py:39`) and implements a focused set of commands. Cross-mixin calls (for example, `JSONMixin` calling `BasicMixin.set`) are declared as stubs on the base class so type checkers can resolve them.

Error types defined in `base.py:24`:

* `ValkeyError` — Base class for all Valkey errors.
* `ValkeyConnectionError` — Connection failures.
* `ValkeyOperationError` — Operation failures when disabled or not connected.

## Prerequisites

* Valkey or Redis server reachable at the configured `host` and `port`.
* `valkey-glide-sync` Python package: `pip install valkey-glide-sync`

If the package is not installed, `connect()` raises `ImportError` with installation instructions. See `base.py:137`.

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

You can also use the client as a context manager:

```python
from src.core.database.valkey_client import ValkeyClient

with ValkeyClient() as client:
    client.set("key", "value")
```

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
from src.core.database.valkey_client import setup, get_client, is_available

# Initialize the shared client from config
client = setup()

# Get the existing shared client
client = get_client()

# Check whether Valkey is available
if is_available():
    client.set("key", "value")
```

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

* `_prefixed_key` (`base.py:207`) adds the configured prefix if it is not already present.
* `_sanitize_key` (`base.py:212`) strips non-printable characters and truncates keys to 512 characters.
* `eval_lua` (`base.py:216`) executes a Lua script with automatic key prefixing.
