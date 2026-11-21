"""Cached rendering of repeated outbound gateway payloads.

Why this exists
---------------
Before: a single event (a presence update is the common case) was rendered for
every recipient with ``stringify_snowflake_ids(event.data)`` — a recursive
rebuild of the whole payload — and again in the inline DISPATCH path of the
gateway loop. A presence update fanned out to thousands of connections paid
that cost thousands of times even though the payload was byte-for-byte
identical for all of them.

After: a small bounded LRU memoizes the rendered ``d`` value by a cheap
structural fingerprint of the payload. Distinct events (different sequence
numbers are added *outside* the cache, on the enclosing frame) share one
rendering, and repeated identical presence payloads skip the recursive walk
entirely. The gateways keep a per-connection cache for frames rendered outside
a fan-out, while the dispatcher keeps one shared cache for fan-outs.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

#: Entries kept per cache. Presence payloads are small; 64 distinct renderings
#: is ample while bounding retention of cached payload structures.
DEFAULT_CACHE_ENTRIES = 64

_MISSING = object()


def freeze_payload(value: Any) -> Any:
    """Return a hashable, structure-preserving key for a JSON-like value.

    This is intentionally not ``json.dumps``: it avoids re-serializing the
    payload just to decide whether it was already rendered. Dict ordering is
    normalized so equal payloads produce equal keys.
    """
    if isinstance(value, dict):
        return (
            "d",
            tuple(sorted((str(k), freeze_payload(v)) for k, v in value.items())),
        )
    if isinstance(value, (list, tuple)):
        return ("l", tuple(freeze_payload(v) for v in value))
    if isinstance(value, (set, frozenset)):
        return ("s", tuple(sorted((freeze_payload(v) for v in value), key=repr)))
    if isinstance(value, (str, int, float, bool, type(None))):
        return ("v", type(value).__name__, value)
    # Unhashable/unusual scalars: degrade to repr rather than crash rendering.
    try:
        hash(value)
    except TypeError:
        return ("r", repr(value))
    return ("v", type(value).__name__, value)


class PayloadRenderCache:
    """Bounded LRU of rendered payloads keyed by :func:`freeze_payload`."""

    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[Any, Any]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._key_builds = 0

    def render(
        self,
        data: Any,
        renderer: Callable[[Any], Any],
        *,
        key: Optional[Any] = None,
    ) -> Any:
        """Return the rendered form of ``data``, computing it at most once.

        ``renderer`` is supplied at call time (rather than captured at
        construction) so the shared snowflake helper stays the single source
        of truth and tests can spy on it.

        ``key`` is the pre-computed :func:`freeze_payload` fingerprint. A
        fan-out renders one identical payload for every recipient; rebuilding
        that structural key once per recipient is O(payload) work that the
        cache itself exists to avoid. Callers that already hold the key can
        pass it here so the walk happens once for the whole fan-out. The
        hit/miss semantics are unchanged: callers still invoke ``render`` once
        per consumer, so cache counters stay comparable across both paths.
        """
        if key is None:
            key = freeze_payload(data)
            self._key_builds += 1
        try:
            hash(key)
        except TypeError:  # pragma: no cover - freeze_payload is hashable
            key = repr(data)

        cached = self._entries.get(key, _MISSING)
        if cached is not _MISSING:
            self._hits += 1
            self._entries.move_to_end(key)
            return cached

        self._misses += 1
        rendered = renderer(data)
        self._entries[key] = rendered
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return rendered

    def stats(self) -> Dict[str, int]:
        """Hit/miss counters and current size, for observability and tests."""
        return {
            "size": len(self._entries),
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "key_builds": self._key_builds,
        }
