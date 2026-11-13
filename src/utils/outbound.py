"""Outbound HTTP requests with an explicit scheme allowlist.

``urllib.request.urlopen`` dispatches on whatever scheme the URL carries, so a
URL that configuration controls can reach ``file:`` or a custom handler that
``urllib`` installed. Every outbound request in the codebase goes through
:func:`urlopen` here, which refuses anything that is not an absolute http(s)
URL before the opener sees it. Callers keep their own host policy (for example
``URLValidator`` for the search backends); this is the transport-level floor.
"""

from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, build_opener

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_OPENER = build_opener()


def urlopen(request: Any, timeout: float = 30) -> Any:
    """Open ``request`` after proving it is an absolute http(s) URL."""
    url = request.full_url if isinstance(request, Request) else str(request)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(f"refusing to open non-http(s) URL: {url!r}")
    return _OPENER.open(request, timeout=timeout)
