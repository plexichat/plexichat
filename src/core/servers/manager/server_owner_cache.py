"""Bounded, db-scoped, epoch-keyed L0 cache for the server owner id.

Why this exists
---------------
The server owner is the root of the role-hierarchy position check.  In the
position-resolution paths (voice mute/deafen/move/disconnect, member/role
moderation, AutoMod exemptions) the owner id is re-read with a raw
``SELECT owner_id FROM srv_servers`` on every authorization decision, even
though it is a server-level fact that changes only on an explicit ownership
transfer.  A burst of moderation actions against one server therefore pays the
same read over and over.  The servers manager has a Valkey ``srv_owner:`` tier,
but these hierarchy paths intentionally do not go through the manager's
``get_server`` (which requires membership and does several count subqueries),
so they never benefit from it.

This module memoizes the owner id for a short epoch.  Its key is the complete
identity of the read: ``(db identity, server_id, rbac server epoch)``.

Safety properties (mirrored by ``src/tests/servers/test_server_owner_cache.py``):

* **Bounded** -- hard entry/byte ceilings plus a short TTL.  A flood of distinct
  servers cannot grow the cache without limit; the TTL backstop bounds how long
  a write from another worker can stay invisible (this is a process-local L0).
* **Db-scoped** -- the key carries a stable token tied to the backing database
  object (``WeakKeyDictionary``), so a fresh/repointed database can never
  observe another database's cached owner id.  It also gives each test database
  its own namespace, preventing cross-test contamination.
* **Epoch-keyed** -- the version is the existing RBAC server epoch, which is
  bumped by every ownership transfer (``_invalidate_owner_cache``).  After a
  transfer the key changes and the old owner id becomes unreachable immediately.
* **Fail closed** -- a miss, an expired entry or any cache error yields the
  ``_MISS`` sentinel so the caller recomputes from the authoritative database.
  The cache never synthesizes or widens an authorization decision.
* **Server-level fact only** -- no user id, credential or token is part of the
  key or the value, so sharing the memo across callers cannot leak one viewer's
  decision to another.  The owner id is public server metadata.
"""

from __future__ import annotations

import itertools
import threading
import weakref
from typing import Any

from src.core.bounded_lru import BoundedLRU

from ..channel_access_cache import get_channel_access_cache

#: Ownership changes are rare and always bump the RBAC epoch; keep a short TTL
#: purely as a backstop for a write performed in another worker.
SERVER_OWNER_TTL_SECONDS = 10.0
SERVER_OWNER_MAX_ENTRIES = 512
SERVER_OWNER_MAX_BYTES = 64 * 1024

#: Distinct from any cached value (owner ids are ints) so a cached owner of 0
#: is a real hit rather than being mistaken for a miss.
_MISS = object()

_TOKEN_LOCK = threading.Lock()
_DB_TOKENS: "weakref.WeakKeyDictionary[Any, int]" = weakref.WeakKeyDictionary()
_TOKEN_COUNTER = itertools.count(1)

_owner_cache = BoundedLRU(
    max_entries=SERVER_OWNER_MAX_ENTRIES,
    max_bytes=SERVER_OWNER_MAX_BYTES,
    ttl_seconds=SERVER_OWNER_TTL_SECONDS,
)


def _db_token(db: Any) -> Any:
    """Return a stable, collision-free identity for ``db`` without row data."""
    try:
        with _TOKEN_LOCK:
            token = _DB_TOKENS.get(db)
            if token is None:
                token = next(_TOKEN_COUNTER)
                _DB_TOKENS[db] = token
            return token
    except TypeError:
        # Unhashable / non-weakrefable double: fall back to identity.  The TTL
        # bounds the window in which an id could be recycled.
        return ("id", id(db))


def _cache_key(db: Any, server_id: Any) -> tuple:
    sid = int(server_id)
    server_epoch = get_channel_access_cache().version(sid, 0)[0]
    return (_db_token(db), sid, server_epoch)


def get_cached_server_owner(db: Any, server_id: Any) -> Any:
    """Return the cached owner id, or ``_MISS`` on a miss/error."""
    try:
        return _owner_cache.get(_cache_key(db, server_id), _MISS)
    except Exception:  # pragma: no cover - defensive; never fail a caller open
        return _MISS


def prime_server_owner(db: Any, server_id: Any, owner_id: Any) -> None:
    """Cache an authoritative owner id for this db/server/epoch."""
    try:
        _owner_cache.set(_cache_key(db, server_id), int(owner_id))
    except Exception:  # pragma: no cover - cache is an accelerator only
        pass


def invalidate_server_owner_cache() -> None:
    """Drop every cached owner id (defence in depth on ownership writes)."""
    _owner_cache.clear()


def server_owner_cache_stats() -> dict:
    """Counters and bound snapshot for regression tests and tooling."""
    return _owner_cache.stats()


__all__ = [
    "get_cached_server_owner",
    "prime_server_owner",
    "invalidate_server_owner_cache",
    "server_owner_cache_stats",
    "SERVER_OWNER_TTL_SECONDS",
    "SERVER_OWNER_MAX_ENTRIES",
    "SERVER_OWNER_MAX_BYTES",
    "_MISS",
]
