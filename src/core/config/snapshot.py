"""Cached, versioned snapshots of configuration data.

The previous pattern in config consumers was a wall-clock TTL: on every
expiry the full read/parse/validate chain ran again even when the underlying
file had not changed, and a real edit stayed invisible for the rest of the
TTL window.  A :class:`VersionedConfigSnapshot` keys its cached value on the
*version* of its source instead -- by default the mtime (nanoseconds) and size
of the files it watches.  An unchanged file is therefore served from memory
forever, while an edit is observed on the very next access with no staleness
window.

The class is deliberately generic: the value producer and the version token
are both callables, so callers can watch files, an in-memory loader instance,
or any other cheap-to-compute version signal.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Hashable, Iterable, Optional, Tuple


def file_version(*paths: Optional[str]) -> Tuple[Optional[Tuple[int, int]], ...]:
    """Build a cheap, comparable version token from file metadata.

    Each path contributes ``(st_mtime_ns, st_size)``.  A missing or unreadable
    path contributes ``None`` rather than raising, so a snapshot over an
    optional file still works before that file is created (and rebuilds the
    moment it appears).
    """
    parts: list[Optional[Tuple[int, int]]] = []
    for path in paths:
        if not path:
            parts.append(None)
            continue
        try:
            stat = os.stat(path)
        except OSError:
            parts.append(None)
        else:
            parts.append((stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


class VersionedConfigSnapshot:
    """A thread-safe cached value rebuilt only when its version changes.

    Args:
        loader: Zero-argument callable producing the value to cache.
        paths: Files whose metadata defines the version. Used when no
            ``version_fn`` is supplied.
        version_fn: Optional callable returning an arbitrary hashable version
            token. Takes precedence over ``paths`` when given.
    """

    def __init__(
        self,
        loader: Callable[[], Dict[str, Any]],
        *,
        paths: Iterable[Optional[str]] = (),
        version_fn: Optional[Callable[[], Hashable]] = None,
    ) -> None:
        self._loader = loader
        self._paths = tuple(str(path) for path in paths if path)
        self._version_fn = version_fn
        self._lock = threading.RLock()
        self._value: Optional[Dict[str, Any]] = None
        self._version: Optional[Hashable] = None
        self._hits = 0
        self._misses = 0
        self._rebuilds = 0

    def _current_version(self) -> Hashable:
        if self._version_fn is not None:
            return self._version_fn()
        return file_version(*self._paths)

    def get(self, key: Any, default: Any = None) -> Any:
        """Return one key from the snapshot, rebuilding it if stale."""
        return self.snapshot().get(key, default)

    def snapshot(self) -> Dict[str, Any]:
        """Return the current value, rebuilding it only when the version moved."""
        with self._lock:
            version = self._current_version()
            if self._value is None or version != self._version:
                self._misses += 1
                self._value = self._loader()
                self._version = version
                self._rebuilds += 1
            else:
                self._hits += 1
            return self._value

    def invalidate(self) -> None:
        """Drop the cached value so the next access rebuilds unconditionally."""
        with self._lock:
            self._value = None
            self._version = None

    @property
    def version(self) -> Optional[Hashable]:
        """The version token the cached value was built against."""
        with self._lock:
            return self._version

    def stats(self) -> Dict[str, Any]:
        """Return hit/miss/rebuild counters plus the current version."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "rebuilds": self._rebuilds,
                "version": self._version,
            }
