"""Bounded, short-TTL cache for admin role-grant lookups.

``get_admin_granted_permissions`` (the authority check behind
``can_delegate_permissions`` / ``can_grant_existing_role``) resolves an admin's
role payloads through ``_get_admin_roles``.  A single privileged request runs
that query more than once -- the permission gate, the delegation check and the
hierarchy helper each need the same role set -- and the rows are fixed for the
lifetime of a request.  This module memoises the *granted scope set* so those
repeated lookups collapse to one database read.

Security invariants (a stale scope set is a stale grant):

* The key is ``(db, credential, subject)``: a monotonic token tied to the
  backing database object (``WeakKeyDictionary``), the caller's credential
  discriminator (a non-secret session fingerprint or a ``direct`` sentinel),
  and the admin id whose roles are resolved.  A fresh/repointed database or a
  different credential can never observe another caller's cached grants.  The
  configured DB path is folded in as a secondary guard where available.
* Entries carry a short TTL and the store is size-bounded with expired/oldest
  eviction, so even a missed invalidation cannot serve a revoked scope for
  longer than :data:`DEFAULT_TTL_SECONDS`.
* Every privilege mutation drops the whole store through both
  ``permissions.invalidate_admin_permission_caches`` (driven by
  ``log_admin_action``) and ``auth.privilege_cache.invalidate_privilege_caches``
  (driven by the admin mutation routes), so a revocation takes effect on the
  very next authorization decision in the same request.
* Values are produced by a caller-supplied loader that fails closed (an empty
  ``frozenset`` on error / no roles).  Nothing is ever synthesised into a grant,
  and a cache hit can only return a previously loaded, fail-closed value.
"""

from __future__ import annotations

import itertools
import threading
import time
import weakref
from typing import Any, Callable, Hashable

_LOCK = threading.Lock()
_TOKEN_LOCK = threading.Lock()

# A stable, collision-free identity per database object.  Weak keys mean a
# collected/replaced db cannot keep its token (or cached grants) alive.
_DB_TOKENS: "weakref.WeakKeyDictionary[Any, int]" = weakref.WeakKeyDictionary()
_TOKEN_COUNTER = itertools.count(1)

# (db token, db path, credential, subject) -> (expiry, granted scopes)
_ENTRIES: dict[tuple[Any, ...], tuple[float, frozenset]] = {}
_MAX_ENTRIES = 512

DEFAULT_TTL_SECONDS = 1.0

# Used when a caller has no request-bound admin session credential (direct
# core calls, self-test identity path, unit tests).  It is a fixed sentinel,
# never attacker-controlled credential material.
DIRECT_CREDENTIAL = "direct"


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


def _normalize(value: Any) -> Hashable:
    """Coerce a key component to a hashable, equality-stable form."""
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def _make_key(db: Any, credential: Any, subject: Any) -> tuple[Any, ...]:
    token, path = _db_identity(db)
    return (
        token,
        path,
        _normalize(credential if credential is not None else DIRECT_CREDENTIAL),
        _normalize(subject),
    )


def get_cached_grants(
    db: Any,
    credential: Any,
    subject: Any,
    loader: Callable[[], frozenset],
    ttl: float = DEFAULT_TTL_SECONDS,
) -> frozenset:
    """Return ``subject``'s granted scopes, loading and caching on a miss.

    ``loader`` performs the actual (fail-closed) database read and must return
    a ``frozenset``.  The TTL and size bound keep the store finite; invalidation
    after privilege mutations is handled by :func:`invalidate_grant_cache`.
    """
    key = _make_key(db, credential, subject)
    now = time.monotonic()

    # Phase 1 - fast, unlocked read on the hot path. Stored values are immutable
    # (expiry, frozenset) tuples; the only mutations are the bounded refill
    # below and an atomic ``clear()`` on invalidation, both under ``_LOCK``. A
    # read can therefore only observe a complete, still-live entry: it never
    # widens a grant, and a call that starts after invalidation sees the empty
    # (or new) store and reloads fail-closed. Keeping the global lock off hits
    # stops every concurrent authorization reader from queueing behind it.
    entry = _ENTRIES.get(key)
    if entry is not None and entry[0] > now:
        return entry[1]

    # Phase 2 - miss (or expired): load outside the lock, then take the lock
    # only for the bounded refill. Re-check under the lock so a value another
    # caller stored while we loaded wins instead of being overwritten.
    value = loader()
    if not isinstance(value, frozenset):
        # Never cache a non-immutable value: a caller could mutate it into a
        # grant after the fact. Fail closed instead.
        value = frozenset()

    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]
        if len(_ENTRIES) >= _MAX_ENTRIES:
            for stale in [k for k, (exp, _) in _ENTRIES.items() if exp <= now]:
                _ENTRIES.pop(stale, None)
            while len(_ENTRIES) >= _MAX_ENTRIES:
                oldest = min(_ENTRIES, key=lambda k: _ENTRIES[k][0])
                _ENTRIES.pop(oldest, None)
        _ENTRIES[key] = (now + ttl, value)
    return value


def invalidate_grant_cache() -> None:
    """Drop every cached grant set (called after a privilege mutation)."""
    with _LOCK:
        _ENTRIES.clear()


# Explicit aliases used by tests / request-boundary tooling.
clear_grant_cache = invalidate_grant_cache
