"""Bounded, db-scoped cache for admin role-hierarchy position lookups.

``get_admin_max_position`` is the hottest remaining role query in the admin
authorization path: ``can_manage_admin`` resolves the actor and the target
(two queries), ``can_manage_role`` resolves the actor again, and a single
management request may repeat those checks for several targets.  The rows are
fixed for the lifetime of a request, so this module memoises
``(db, admin_id) -> max(position)`` for a short window.

Security invariants (a stale *higher* position is a stale grant):

* The cache is keyed by a monotonic token tied to the backing database object
  (``WeakKeyDictionary``), so a fresh/repointed database can never observe
  another database's cached positions.  The configured DB path is folded in as
  a secondary guard where available.
* Entries carry a short TTL and the store is size-bounded with expired/oldest
  eviction, so even a missed invalidation cannot serve a revoked position for
  longer than :data:`DEFAULT_TTL_SECONDS`.
* Every privilege mutation drops the whole store through
  ``permissions.invalidate_admin_permission_caches`` (the single choke point
  driven by ``log_admin_action``).  A revocation therefore takes effect on the
  very next authorization decision in the same request.
* Values are produced by a caller-supplied loader that fails closed (returns
  ``0`` on error / no roles).  Nothing is ever synthesised into a grant.
"""

from __future__ import annotations

import itertools
import threading
import time
import weakref
from typing import Any, Callable

_LOCK = threading.Lock()
_TOKEN_LOCK = threading.Lock()

# A stable, collision-free identity per database object.  Weak keys mean a
# collected/replaced db cannot keep its token (or cached rows) alive.
_DB_TOKENS: "weakref.WeakKeyDictionary[Any, int]" = weakref.WeakKeyDictionary()
_TOKEN_COUNTER = itertools.count(1)

# (db token, db path, admin id) -> (expiry, max position)
_ENTRIES: dict[tuple[Any, ...], tuple[float, int]] = {}
_MAX_ENTRIES = 512

DEFAULT_TTL_SECONDS = 1.0


def _db_identity(db: Any) -> tuple[Any, Any]:
    """Return a stable identity for ``db`` without caching any row data."""
    path = None
    cfg = getattr(db, "config", None)
    if isinstance(cfg, dict):
        path = cfg.get("path")
    try:
        with _TOKEN_LOCK:
            token = _DB_TOKENS.get(db)
            if token is None:
                token = next(_TOKEN_COUNTER)
                _DB_TOKENS[db] = token
    except TypeError:
        # Unhashable / non-weakrefable double: fall back to identity. The TTL
        # bounds the window in which an id could be recycled.
        token = id(db)
    return (token, path)


def _make_key(db: Any, admin_id: int) -> tuple[Any, ...]:
    token, path = _db_identity(db)
    return (token, path, admin_id)


def get_cached_max_position(
    db: Any,
    admin_id: int,
    loader: Callable[[], int],
    ttl: float = DEFAULT_TTL_SECONDS,
) -> int:
    """Return ``admin_id``'s max role position, loading and caching on a miss.

    ``loader`` performs the actual (fail-closed) database read.  The TTL and
    size bound keep the store finite; invalidations are handled separately by
    :func:`invalidate_position_cache`.
    """
    key = _make_key(db, admin_id)
    now = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]

    value = loader()

    with _LOCK:
        if len(_ENTRIES) >= _MAX_ENTRIES:
            for stale in [k for k, (exp, _) in _ENTRIES.items() if exp <= now]:
                _ENTRIES.pop(stale, None)
            while len(_ENTRIES) >= _MAX_ENTRIES:
                oldest = min(_ENTRIES, key=lambda k: _ENTRIES[k][0])
                _ENTRIES.pop(oldest, None)
        _ENTRIES[key] = (now + ttl, value)
    return value


def invalidate_position_cache() -> None:
    """Drop every cached position (called after a privilege mutation)."""
    with _LOCK:
        _ENTRIES.clear()


# Explicit alias used by tests / request-boundary tooling.
clear_position_cache = invalidate_position_cache
