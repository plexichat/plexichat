# Redis Client Sub-package

Refactored from a monolithic `redis_client.py` into a mixin-based architecture.

## Structure

```
redis_client/
├── __init__.py    - Package entry point; re-exports RedisClient + module-level helpers
├── base.py        - RedisClientBase: __init__, connection management, shared state,
│                     type-annotated attributes, cross-mixin stubs, error classes
├── basic.py       - BasicMixin: get, set, delete, exists, expire, ttl
├── json.py        - JSONMixin: json_get, json_set, json_delete, json_merge
├── hash.py        - HashMixin: hget, hset, hdel, hgetall, hmset
├── list.py        - ListMixin: lpush, rpush, lpop, rpop, lrange, llen, ltrim
├── set.py         - SetMixin: sadd, srem, smembers, sismember
├── counter.py     - CounterMixin: incr, decr
├── pubsub.py      - PubSubMixin: publish, subscribe, unsubscribe
├── lock.py        - LockMixin: acquire_lock, release_lock
├── admin.py       - AdminMixin: ping, health_check, flush_prefix, keys
├── composer.py    - RedisClient combining all mixins via multiple inheritance
└── README.md      - This file
```

## Architecture

Each mixin class inherits from `RedisClientBase` and provides a focused set of Redis operations.
The `RedisClient` class in `composer.py` combines all mixins via multiple inheritance (MRO).

Cross-mixin calls (e.g., `JSONMixin.set_json` calling `BasicMixin.set`) are declared as stub
methods on `RedisClientBase` so type checkers can resolve them. At runtime, Python's MRO
handles dispatch to the correct mixin implementation.

## Usage

```python
from src.core.database.redis_client import RedisClient

client = RedisClient()
client.connect()
client.set("key", "value", ttl=300)
value = client.get("key")
client.close()
```

Module-level convenience functions are also available:

```python
from src.core.database.redis_client import setup, get_client, is_available

client = setup()
client = get_client()
if is_available():
    ...
```
