"""Request-scoped coalescing of identical cache lookups.

A single request can resolve the same read-only cache key from several layers
(auth, permission checks, serializers). Every ``cache_get`` is a Valkey GET plus
object reconstruction, so repeating the identical lookup inside one request is
pure overhead. The helpers here memoise the *result of the first lookup of a
key within one request* so the later, identical lookups are served from a tiny
request-local dict instead of going back to the backing store.

Scope is the ASGI ``RequestContext`` that ``AuthenticationMiddleware`` already
binds to the current task (HTTP and WebSocket scopes). When there is no request
context, callers can opt in with :func:`request_cache_scope` (background jobs,
tests). Without either, lookups pass straight through, so non-request code and
existing tests keep their exact current behaviour.

Nothing survives the request boundary: a fresh store per request means no TTL
and no cross-request leakage. A short freshness window bounds how long a
coalesced *value* may be reused, and a hard entry cap bounds the store's memory
in long-lived scopes such as a WebSocket connection (where the ASGI scope --
and therefore this memo -- lives for the whole socket). Any delete of a key
(through ``cache_delete`` -> ``mem_cache_delete``) drops its coalesced copy, and
writers in this package invalidate explicitly, so a write-then-read in the same
request never observes a stale memoised value.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, Optional

# (created_at, value). The timestamp bounds staleness in scopes that outlive a
# single request (notably WebSockets): a coalesced read is only reused for this
# many seconds even when the request context itself is still bound.
_COALESCE_MAX_AGE_SECONDS = 1.0

# Hard cap on memoised keys per scope. HTTP scopes die with the request, but a
# WebSocket scope lives for the whole connection. Rendering fan-out for many
# users used to add one permanent entry per distinct ``presence:<user>`` /
# ``session:<id>`` key to that scope, so a single long-lived socket could pin
# thousands of values. Bounded LRU keeps hot keys and drops the oldest past the
# cap; an evicted key simply costs one fresh backing read on its next lookup.
# The cap is kept small (a single request resolves far fewer than this many
# distinct keys) and is well below any per-connection memory budget.
_COALESCE_MAX_ENTRIES = 256

_ATTR = "_cache_lookups"
_FALLBACK_STORE: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "plexichat_cache_request_lookups", default=None
)


def _request_context() -> Optional[Any]:
    """Return the bound ASGI RequestContext, without importing the API layer.

    ``src.api.middleware.request_context`` is loaded at app startup; looking it
    up in ``sys.modules`` avoids a core -> api import (and any cycle) while
    still reusing the exact context the middleware bound to this task. In
    non-ASGI code the module is absent and we fall back to our own ContextVar.
    """
    module = sys.modules.get("src.api.middleware.request_context")
    if module is None:
        return None
    getter = getattr(module, "current_context", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None


def _store(create: bool) -> Optional[Dict[str, Any]]:
    """Return the active request-local store, creating it when asked."""
    ctx = _request_context()
    if ctx is not None:
        store = getattr(ctx, _ATTR, None)
        if not isinstance(store, dict):
            if not create:
                return None
            try:
                store = {}
                setattr(ctx, _ATTR, store)
            except Exception:
                return None
        return store

    # Outside ASGI: only use an explicitly opened scope. Never allocate an
    # unbounded per-task store implicitly in a long-lived worker.
    return _FALLBACK_STORE.get()


def _remember(store: Dict[str, Any], key: str, entry: Any) -> None:
    """Store ``entry`` under ``key``, evicting the oldest keys past the cap.

    Re-inserting an existing key moves it to the end, so a key touched on a hot
    hit becomes the most-recently used and survives future sweeps. The original
    ``created_at`` is preserved: refresh position, never the freshness window.
    ``dict`` preserves insertion order, so the leading keys are the oldest.
    """
    store.pop(key, None)
    store[key] = entry
    overflow = len(store) - _COALESCE_MAX_ENTRIES
    if overflow > 0:
        for oldest in list(store)[:overflow]:
            store.pop(oldest, None)


def coalesced_cache_get(key: str, *, loader: Optional[Callable[[], Any]] = None) -> Any:
    """Return a cache value, issuing at most one backing read per key/request.

    ``None`` (a cache miss) is memoised too: a second lookup of the same absent
    key in one request must not trigger a second Valkey round-trip either.
    ``loader`` defaults to ``operations.cache_get(key)`` and is injectable for
    tests and alternate backends. The memo is a bounded LRU: in a long-lived
    scope the least-recently used keys are dropped once ``_COALESCE_MAX_ENTRIES``
    is exceeded. Eviction never changes *what* a key returns -- a recomputed
    value comes from that key's own loader, so no value crosses user/scope
    boundaries.
    """
    store = _store(create=True)
    if store is None:
        return loader() if loader is not None else _default_loader(key)

    entry = store.get(key)
    now = time.monotonic()
    if entry is not None:
        created_at, value = entry
        if now - created_at < _COALESCE_MAX_AGE_SECONDS:
            _remember(store, key, entry)
            return value

    value = loader() if loader is not None else _default_loader(key)
    _remember(store, key, (now, value))
    return value


def _default_loader(key: str) -> Any:
    from .operations import cache_get

    return cache_get(key)


def invalidate_request_key(key: str) -> None:
    """Drop one key's coalesced copy from the active request scope."""
    store = _store(create=False)
    if store is not None:
        store.pop(key, None)


def invalidate_request_prefix(prefix: str) -> None:
    """Drop every coalesced key starting with ``prefix`` from this scope."""
    store = _store(create=False)
    if store is not None:
        for key in [k for k in store if k.startswith(prefix)]:
            store.pop(key, None)


def clear_request_cache() -> None:
    """Drop all coalesced lookups from the active request scope."""
    store = _store(create=False)
    if store is not None:
        store.clear()


@contextmanager
def request_cache_scope() -> Iterator[Dict[str, Any]]:
    """Open an explicit request-local coalescing scope (non-ASGI callers).

    Yields the fresh store so tests can introspect it; the previous ContextVar
    value is always restored on exit, including on exceptions.
    """
    store: Dict[str, Any] = {}
    token = _FALLBACK_STORE.set(store)
    try:
        yield store
    finally:
        _FALLBACK_STORE.reset(token)


__all__ = [
    "clear_request_cache",
    "coalesced_cache_get",
    "invalidate_request_key",
    "invalidate_request_prefix",
    "request_cache_scope",
]
