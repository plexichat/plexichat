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
"""

from __future__ import annotations

import re
from typing import Any, Optional

# "METHOD /path" (method may carry a namespace prefix, e.g. ``decorator:POST``).
# Symbolic keys such as ``THUMBNAIL_GEN`` or ``static_client:html`` have no
# space and are intentionally left untouched.
_ROUTE_WITH_METHOD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_:.-]*)[ \t]+(/.*)$")


def canonicalize_route(route: Any) -> Any:
    """Return a canonical ``"METHOD /route"`` pattern for a caller-supplied route.

    Non-string, empty and symbolic (non-path) values pass through unchanged;
    string values that look like ``"METHOD /path"`` are normalized through the
    middleware's route table, which strips query strings, collapses id-like
    segments and bounds the unknown-route keyspace.
    """
    if not isinstance(route, str) or not route:
        return route
    match = _ROUTE_WITH_METHOD_RE.match(route)
    if match is None:
        return route
    method, path = match.group(1), match.group(2)
    from ..middleware import extract_route_info

    canonical: Optional[str]
    canonical, _, _ = extract_route_info(path, method)
    return canonical or route


__all__ = ["canonicalize_route"]
