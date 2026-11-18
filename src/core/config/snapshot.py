"""Cached, versioned snapshots of configuration data.

The previous pattern in config consumers was a wall-clock TTL: on every
expiry the full read/parse/validate chain ran again even when the underlying
file had not changed, and a real edit stayed invisible for the rest of the
TTL window.  A :class:`VersionedConfigSnapshot` keys its cached value on the
*version* of its source instead -- by default the mtime (nanoseconds) and size
of the files it watches.  An unchanged file is therefore served from memory
forever, while an edit is observed on the next version check.  Callers may also
set a short ``max_age`` staleness ceiling to throttle those checks (and the
filesystem stat they imply) on very hot paths.

The class is deliberately generic: the value producer and the version token
are both callables, so callers can watch files, an in-memory loader instance,
or any other cheap-to-compute version signal.
"""

from __future__ import annotations

import os
import threading
import time
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


def config_source_version(*extra: Hashable) -> Tuple[Any, ...]:
    """Version token for values derived from the live config source.

    The token folds in everything that can change the meaning of a
    config-derived value:

    * the identity of the live ``utils.config`` loader -- a new
      ``config.setup()`` builds a new instance and therefore a new token;
    * the mtime/size of that loader's file -- an on-disk edit is observed on
      the next version check;
    * any caller-supplied ``extra`` parts (e.g. the section being cached).

    ``utils.config`` is imported lazily so importing this helper never forces
    config setup and cannot create an import cycle.  Callers typically hand
    the result to :class:`VersionedConfigSnapshot` as its ``version_fn``.
    """
    try:
        import utils.config as config
    except Exception:  # config utility unavailable (unlikely in the app)
        loader = None
    else:
        loader = getattr(config, "_config_instance", None)
    path = getattr(loader, "config_path", None)
    return (id(loader), file_version(path), *extra)


class VersionedConfigSnapshot:
    """A thread-safe cached value rebuilt only when its version changes.

    A version check can itself be expensive: the common ``file_version`` token
    stats the config file, so an unthrottled snapshot performs a filesystem
    read on every request.  ``max_age`` installs a short *staleness ceiling*:
    once a version has been checked, later accesses within that window are
    served straight from memory with no version check at all.  After the
    ceiling the version is re-evaluated; an unchanged source keeps the cached
    value (no rebuild), while a changed source rebuilds on that access.  This
    bounds how long a change can go unnoticed without paying the check cost on
    every call.  ``invalidate()`` remains available for immediate invalidation
    (e.g. from an admin config-reload path).

    Args:
        loader: Zero-argument callable producing the value to cache.
        paths: Files whose metadata defines the version. Used when no
            ``version_fn`` is supplied.
        version_fn: Optional callable returning an arbitrary hashable version
            token. Takes precedence over ``paths`` when given.
        max_age: Optional staleness ceiling in seconds. ``None`` (the default)
            preserves the original behaviour of checking the version on every
            access; a positive value throttles those checks to at most one per
            ``max_age`` seconds.
    """

    def __init__(
        self,
        loader: Callable[[], Dict[str, Any]],
        *,
        paths: Iterable[Optional[str]] = (),
        version_fn: Optional[Callable[[], Hashable]] = None,
        max_age: Optional[float] = None,
    ) -> None:
        self._loader = loader
        self._paths = tuple(str(path) for path in paths if path)
        self._version_fn = version_fn
        # A non-positive ceiling is meaningless; treat it as "no ceiling" so
        # bad config cannot turn into a version check on every single access.
        self._max_age: Optional[float] = (
            float(max_age) if max_age is not None and max_age > 0 else None
        )
        self._lock = threading.RLock()
        self._value: Optional[Dict[str, Any]] = None
        self._version: Optional[Hashable] = None
        self._version_checked_at: Optional[float] = None
        self._hits = 0
        self._misses = 0
        self._rebuilds = 0
        self._version_checks = 0

    def _current_version(self) -> Hashable:
        if self._version_fn is not None:
            return self._version_fn()
        return file_version(*self._paths)

    def get(self, key: Any, default: Any = None) -> Any:
        """Return one key from the snapshot, rebuilding it if stale."""
        return self.snapshot().get(key, default)

    def snapshot(self) -> Dict[str, Any]:
        """Return the current value, rebuilding it only when the version moved.

        A rebuild runs the (potentially expensive) loader and is counted as a
        miss.  A cheap version check is performed on every access unless a
        ``max_age`` ceiling is active, in which case the check itself is
        throttled to at most once per ceiling window.
        """
        with self._lock:
            now = time.monotonic()
            if self._value is not None:
                # The staleness ceiling lets the hot path skip even the
                # version check (which stats the config file) for a short
                # window.  Version identity still governs rebuilds once we
                # do check; the ceiling only bounds how often we pay for it
                # and guarantees a periodic refresh even if the token misses
                # an in-place mutation of the source.
                within_ceiling = (
                    self._max_age is not None
                    and self._version_checked_at is not None
                    and (now - self._version_checked_at) < self._max_age
                )
                if within_ceiling:
                    self._hits += 1
                    return self._value

                self._version_checks += 1
                version = self._current_version()
                self._version_checked_at = now
                if version == self._version:
                    # Ceiling elapsed but nothing changed: refresh the check
                    # timestamp, keep the value, do not run the loader.
                    self._hits += 1
                    return self._value
            else:
                self._version_checks += 1
                version = self._current_version()
                self._version_checked_at = now

            self._misses += 1
            self._value = self._loader()
            self._version = version
            self._rebuilds += 1
            return self._value

    def invalidate(self) -> None:
        """Drop the cached value so the next access rebuilds unconditionally.

        Also clears the staleness-ceiling clock so the next access performs a
        real version check rather than reusing a throttled timestamp.
        """
        with self._lock:
            self._value = None
            self._version = None
            self._version_checked_at = None

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
                "version_checks": self._version_checks,
                "max_age": self._max_age,
            }
