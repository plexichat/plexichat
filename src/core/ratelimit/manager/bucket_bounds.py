"""Bounds on the number of live rate-limit buckets.

Key *length* is bounded in :mod:`.keys`; this module bounds key *count*. A
caller that can vary a bucket key (a forged user id, a rotating IP, an
unknown route) must not be able to grow the in-memory bucket map without
limit. ``BoundedBucketMap`` keeps at most ``capacity`` entries and evicts the
least-recently-used one on insert, which is the same shape as the TTL/row-count
eviction the persistent backends already perform.

The class is deliberately tiny and dependency-free so it can wrap any storage
dict (memory backend today, a cache of handles to the DB/Valkey backends
tomorrow) without changing those backends' contracts.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterator, MutableMapping

#: Hard ceiling on buckets tracked per limiter instance. Documented scopes
#: fit comfortably inside this; the figure only exists to stop runaway growth.
MAX_BUCKETS = 100_000


class BoundedBucketMap(MutableMapping[str, Any]):
    """An insertion/LRU-ordered map that never holds more than ``capacity`` keys."""

    def __init__(self, capacity: int = MAX_BUCKETS) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("bucket capacity must be an integer")
        if capacity < 1 or capacity > MAX_BUCKETS:
            raise ValueError(f"bucket capacity must be in [1, {MAX_BUCKETS}]")
        self.capacity = capacity
        self._data: "OrderedDict[str, Any]" = OrderedDict()
        # Count evictions so operators/tests can see that the bound engaged.
        self.evictions = 0

    def __getitem__(self, key: str) -> Any:
        value = self._data[key]
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("bucket key must be a non-empty string")
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)
            self.evictions += 1

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()


def bound_existing_buckets(
    buckets: MutableMapping[str, Any], capacity: int = MAX_BUCKETS
) -> BoundedBucketMap:
    """Copy ``buckets`` into a ``BoundedBucketMap``, keeping the newest entries.

    Useful when a limiter was constructed before the cap existed: the existing
    rows are brought under the bound immediately instead of lingering forever.
    """
    result = BoundedBucketMap(capacity)
    for key, value in buckets.items():
        result[key] = value
    return result


__all__ = ["MAX_BUCKETS", "BoundedBucketMap", "bound_existing_buckets"]
