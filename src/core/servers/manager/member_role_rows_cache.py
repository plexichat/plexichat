"""Bounded, role-version-keyed L1 cache for member role rows.

Why this exists
---------------
``CacheOpsMixin._get_member_role_rows`` resolves a member's roles with a join
over ``srv_member_roles`` / ``srv_roles``. It is an authorization primitive:
role hierarchy checks, permission resolution, bans, voice moderation and
automod all call it, often several times while hydrating a single request (a
channel listing resolves permissions for every channel, and each role
override needs the caller's role positions). Each call used to pay for a fresh
``SELECT`` even when nothing about that member's roles had changed.

This module memoizes the resulting row list for a short epoch. Its key is the
complete identity of the decision: ``(server_id, user_id, server_epoch,
user_epoch)``. The epochs are the ones already maintained by
:class:`~src.core.servers.channel_access_cache.ChannelAccessCache`, so every
existing role / membership / override / ownership mutation retires the cached
rows *immediately* -- no new invalidation hook is needed on the write paths.

Safety properties (mirrored by ``src/tests/servers/test_member_role_rows_cache.py``):

* **Bounded** -- hard entry/byte ceilings plus a short TTL. A flood of distinct
  user ids cannot grow the cache without limit; a TTL backstop bounds how long
  a write from another worker can stay invisible (this is a process-local L0).
* **Keyed by the complete identity** -- server and user are in the key together
  with the role epoch. One member's roles can never be served for another, and
  a stale epoch can never be served after a revocation.
* **Fail closed** -- a miss, an expired entry or any cache error yields the
  ``_MISS`` sentinel so the caller recomputes from the authoritative database.
  The cache never synthesizes a role or widens an authorization decision.
* **Immutable values, no per-read copy** -- rows are frozen into a tuple of
  ``MappingProxyType`` mappings (including nested dicts/lists such as the
  ``permissions`` vocabulary) when stored. Every hit returns the *same* frozen
  object instead of rebuilding a dict per row per authorization check. A
  caller mutation attempt raises ``TypeError`` rather than silently editing a
  detached copy, so it can never poison a later decision -- and no allocation
  is spent defending against a mutation that cannot succeed.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Dict, List, cast

from src.core.bounded_lru import BoundedLRU

from ..channel_access_cache import get_channel_access_cache

#: Authorization rows are cheap to re-read but hot; keep the window shorter
#: than the 60s ``perms:`` tier so another worker's write converges quickly.
MEMBER_ROLE_ROWS_TTL_SECONDS = 10.0
MEMBER_ROLE_ROWS_MAX_ENTRIES = 2048
MEMBER_ROLE_ROWS_MAX_BYTES = 256 * 1024

#: Distinct from any possible cached value (always a tuple) so a cached empty
#: role list is a real hit rather than being mistaken for a miss.
_MISS = object()

_RoleRows = List[Dict[str, Any]]

_member_role_rows_cache = BoundedLRU(
    max_entries=MEMBER_ROLE_ROWS_MAX_ENTRIES,
    max_bytes=MEMBER_ROLE_ROWS_MAX_BYTES,
    ttl_seconds=MEMBER_ROLE_ROWS_TTL_SECONDS,
)


def _freeze(value: Any) -> Any:
    """Recursively freeze a row into immutable mappings/sequences.

    Role rows carry nested mutable payloads -- most importantly the permission
    vocabulary under ``permissions``. Freezing only the outer dict would let a
    caller reach through ``row["permissions"]`` and widen a cached grant, so
    nested dicts become ``MappingProxyType`` and nested lists become tuples.
    """
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_rows(rows: _RoleRows) -> tuple:
    """Normalize authoritative rows into the immutable cached value shape."""
    return tuple(_freeze(dict(row)) for row in rows)


def _cache_key(server_id: Any, user_id: Any) -> tuple:
    """Full identity of a member-roles read, including the RBAC epoch.

    The version is derived on every call rather than supplied by a caller, so
    a stale version can never be presented to reuse a revoked role set: after
    a bump the key simply changes and the old entry is unreachable.
    """
    sid, uid = int(server_id), int(user_id)
    server_epoch, user_epoch = get_channel_access_cache().version(sid, uid)
    return (sid, uid, server_epoch, user_epoch)


def get_cached_member_role_rows(server_id: Any, user_id: Any) -> Any:
    """Return the cached frozen role rows, or ``_MISS`` on a miss.

    Any cache failure is treated as a miss: callers resolve from the database
    on ``_MISS``, so a broken cache can only cost a query, never grant access.

    Values are immutable, so the frozen tuple is handed straight back; the
    ``tuple(...)`` call is idempotent for an already-frozen value (it returns
    the same object, allocating nothing) and also normalizes any unexpected
    cached shape without ever exposing a mutable row to a caller.
    """
    try:
        value = _member_role_rows_cache.get(_cache_key(server_id, user_id), _MISS)
    except Exception:  # pragma: no cover - defensive; never fail a caller open
        return _MISS
    if value is _MISS or not isinstance(value, tuple):
        # Only rows this module froze are served; a malformed/corrupt entry is
        # a miss, so the caller recomputes fail-closed from the database.
        return _MISS
    return tuple(value)


def prime_member_role_rows(
    server_id: Any, user_id: Any, rows: _RoleRows
) -> List[Dict[str, Any]]:
    """Cache a deeply immutable snapshot of an authoritative role-row list.

    Returns the frozen snapshot so an authoritative (miss) read can hand back
    the exact same immutable value a later cache hit returns -- hit and miss
    are indistinguishable to callers, and neither can be mutated into a grant.
    The value is a read-only sequence of mapping rows; every consumer only
    indexes/iterates it, so it satisfies the historical ``List[Dict]`` contract
    (the cast keeps that public annotation stable for callers).
    """
    frozen = _freeze_rows(rows)
    try:
        _member_role_rows_cache.set(_cache_key(server_id, user_id), frozen)
    except Exception:  # pragma: no cover - cache is an accelerator only
        pass
    return cast(List[Dict[str, Any]], frozen)


def invalidate_member_role_rows_cache() -> None:
    """Drop every cached role row set (defence in depth on membership writes).

    Epoch bumps on the RBAC mutation paths already retire entries by key; this
    full flush is an extra guard for mutation helpers that only clear the
    legacy Valkey tiers. A mutation is rare compared with role reads, so
    clearing this small bounded namespace is cheap and conservative.
    """
    _member_role_rows_cache.clear()


def member_role_rows_cache_stats() -> dict:
    """Counters and bound snapshot for regression tests and tooling."""
    return _member_role_rows_cache.stats()


__all__ = [
    "get_cached_member_role_rows",
    "prime_member_role_rows",
    "invalidate_member_role_rows_cache",
    "member_role_rows_cache_stats",
    "MEMBER_ROLE_ROWS_TTL_SECONDS",
    "MEMBER_ROLE_ROWS_MAX_ENTRIES",
    "MEMBER_ROLE_ROWS_MAX_BYTES",
    "_MISS",
]
