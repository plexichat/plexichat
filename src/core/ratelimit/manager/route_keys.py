"""Canonicalize caller-supplied route strings before they become bucket keys.

The ASGI middleware normalizes a request path through
:func:`src.core.ratelimit.middleware.extract_route_info` before handing it to
the manager. Other callers do not:

* the authentication middleware's IP fallback passes
  ``f"{method} {request.url.path}"`` verbatim;
* the ``@rate_limit`` decorator uses ``request.url.path``, which contains
  concrete path parameters.

If such a string reaches key generation unchanged, every distinct path (or
every distinct id embedded in it) produces a brand-new bucket. A client can
then evade a strict per-route cap simply by varying a harmless path segment,
and the route's configured limit is never found because the lookup misses.

This module applies the *same* normalization at the manager boundary so every
caller is safe regardless of how it invokes the limiter. It reuses
``extract_route_info`` rather than duplicating its route table, so the two
paths cannot drift apart. The import is deliberately lazy to avoid a cycle at
module import time (``middleware`` imports from ``manager.results``).

Scaling
-------
``extract_route_info`` scans the whole route table and, for anything it does
not recognise, acquires the process-wide unknown-route lock. The manager
re-canonicalizes a route that the middleware has *already* canonicalized, so
every request paid that cost a second time -- even for the small, fixed set of
canonical routes ("GET /users/{id}", ...). The result is memoized in a bounded
LRU so repeated requests reuse it instead of re-scanning the table and
re-contending on the lock. The cache is bounded because the input can be an
attacker-controlled raw URL, and the resolved API prefix is part of the key so
a config change is observed immediately instead of serving a stale route.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Optional

# "METHOD /path" (method may carry a namespace prefix, e.g. ``decorator:POST``).
# Symbolic keys such as ``THUMBNAIL_GEN`` or ``static_client:html`` have no
# space and are intentionally left untouched.
_ROUTE_WITH_METHOD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_:.-]*)[ \t]+(/.*)$")

#: Hard bound on the memo. Raw caller paths embed ids and query strings, so
#: their keyspace is unbounded; an LRU keeps memory flat under a crafted flood.
#: Canonical routes are a small, fixed set and stay resident.
CANONICALIZE_CACHE_MAXSIZE = 4096


def _api_prefix_token() -> str:
    """Return the effective API prefix, used only to invalidate the memo.

    The middleware owns the prefix snapshot (versioned on the config source).
    Reusing it here means a renamed prefix moves the cache key on the next
    check, so canonicalization can never return a route derived from an old
    prefix. A defensive fallback keeps canonicalization working if the
    middleware helper is unavailable.
    """
    try:
        from ..middleware import _resolve_api_prefix

        return _resolve_api_prefix()
    except Exception:
        return ""


@lru_cache(maxsize=CANONICALIZE_CACHE_MAXSIZE)
def _canonicalize_string(route: str, api_prefix: str) -> str:
    """Normalize one string route. ``api_prefix`` participates in the key
    solely so config changes invalidate the memo; the extraction below always
    resolves the prefix itself, so the two can never disagree."""
    match = _ROUTE_WITH_METHOD_RE.match(route)
    if match is None:
        return route
    method, path = match.group(1), match.group(2)
    from ..middleware import extract_route_info

    canonical: Optional[str]
    canonical, _, _ = extract_route_info(path, method)
    return canonical or route


def canonicalize_route(route: Any) -> Any:
    """Return a canonical ``"METHOD /route"`` pattern for a caller-supplied route.

    Non-string, empty and symbolic (non-path) values pass through unchanged;
    string values that look like ``"METHOD /path"`` are normalized through the
    middleware's route table, which strips query strings, collapses id-like
    segments and bounds the unknown-route keyspace. The expensive lookup is
    memoized in a bounded LRU; results are pure functions of the input and the
    API prefix, so every worker derives identical bucket keys.
    """
    if not isinstance(route, str) or not route:
        return route
    return _canonicalize_string(route, _api_prefix_token())


def clear_canonicalize_cache() -> None:
    """Drop memoized canonicalizations (tests / config-reload callers)."""
    _canonicalize_string.cache_clear()


__all__ = [
    "canonicalize_route",
    "clear_canonicalize_cache",
    "CANONICALIZE_CACHE_MAXSIZE",
]
