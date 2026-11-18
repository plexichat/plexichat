"""Short-TTL, database-scoped cache for static admin read responses.

The role catalog, an admin's role assignments and the approval list are
read-only surfaces that the admin UI polls frequently.  Building their JSON
payload (query + row serialization + FastAPI's recursive encoder) is the bulk
of the work on a warm request even though the underlying rows rarely change.

This module caches the *fully rendered* JSON bytes for a short window.  A hit
returns a ready ``Response`` body, skipping the query, the per-row serializer
and the framework's ``jsonable_encoder`` pass entirely.

Security invariants (never weaken the auth gates):

* Callers MUST run ``check_host_restriction`` and the permission-scope gate
  *before* consulting this cache.  A cached body is therefore only ever served
  to a caller that has been authorized on the current request.
* Cache keys are scoped to the backing database object through a monotonic
  token (a ``WeakKeyDictionary`` keyed by the db), so repointing the DB or
  running against a fresh test database can never observe another database's
  cached payload.  The configured DB path is mixed into the key as well.
* Every mutation of the underlying rows invalidates the relevant namespace
  (see ``invalidate_admin_read_caches`` / ``invalidate_approval_caches``), and
  the TTL is deliberately short so that even a missed invalidation cannot
  serve stale privilege data for more than a couple of seconds.
"""

from __future__ import annotations

import itertools
import threading
import time
import weakref
from typing import Any, Callable, Hashable

_LOCK = threading.Lock()
_TOKENS_LOCK = threading.Lock()
_DB_TOKENS: "weakref.WeakKeyDictionary[Any, int]" = weakref.WeakKeyDictionary()
_TOKEN_COUNTER = itertools.count(1)

# (namespace, db token, db path, params) -> (expiry, rendered bytes)
_ENTRIES: dict[tuple[Any, ...], tuple[float, bytes]] = {}
_MAX_ENTRIES = 256

DEFAULT_TTL_SECONDS = 1.0


def _db_token() -> tuple[Any, Any]:
    """Return a stable identity for the current database, with no row caching."""
    try:
        import src.api as api

        db = api.get_db()
        if db is None:
            return (None, None)
        cfg = getattr(db, "config", None)
        path = cfg.get("path") if isinstance(cfg, dict) else None
        try:
            with _TOKENS_LOCK:
                token = _DB_TOKENS.get(db)
                if token is None:
                    token = next(_TOKEN_COUNTER)
                    _DB_TOKENS[db] = token
        except TypeError:
            # Non-weakrefable/unhashable DB double: fall back to id + path.
            token = id(db)
        return (token, path)
    except Exception:
        return (None, None)


def _make_key(namespace: str, params: Hashable) -> tuple[Any, ...]:
    token, path = _db_token()
    return (namespace, token, path, params)


def get_or_render(
    namespace: str,
    params: Hashable,
    render: Callable[[], bytes],
    ttl: float = DEFAULT_TTL_SECONDS,
) -> bytes:
    """Return cached rendered JSON bytes, computing and caching them on a miss."""
    key = _make_key(namespace, params)
    now = time.monotonic()
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]

    body = render()

    with _LOCK:
        if len(_ENTRIES) >= _MAX_ENTRIES:
            for stale in [k for k, (exp, _) in _ENTRIES.items() if exp <= now]:
                _ENTRIES.pop(stale, None)
            while len(_ENTRIES) >= _MAX_ENTRIES:
                oldest = min(_ENTRIES, key=lambda k: _ENTRIES[k][0])
                _ENTRIES.pop(oldest, None)
        _ENTRIES[key] = (now + ttl, body)
    return body


def invalidate(namespace: str | None = None) -> None:
    """Drop cached entries, optionally only those in ``namespace``."""
    with _LOCK:
        if namespace is None:
            _ENTRIES.clear()
            return
        for key in [k for k in _ENTRIES if k[0] == namespace]:
            _ENTRIES.pop(key, None)


def invalidate_admin_read_caches() -> None:
    """Call from every role mutation after a successful write."""
    invalidate("roles")
    invalidate("admin_roles")


def invalidate_approval_caches() -> None:
    """Call from every approval mutation after a successful write."""
    invalidate("approvals")


def clear_all() -> None:
    """Test helper: drop every cached entry."""
    invalidate(None)


def _render_json(content: Any) -> bytes:
    """Render a payload exactly as FastAPI's ``JSONResponse`` would."""
    from starlette.responses import JSONResponse

    return JSONResponse(content=content).body
