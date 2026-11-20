"""
Bounded cache of the *stable* identity metadata of media files.

A single request often touches the same media file more than once: e.g. the
stash/grants and attachment-serving paths first resolve a file (``get_file`` /
``get_file_by_filename``) and then run ``check_file_access`` on it. Both reads
used to hit ``media_files`` for the same ``id``/``uploaded_by``/``checksum``
triple. This module memoises exactly that triple so those repeated reads become
in-process lookups.

Only fields that cannot change during the lifetime of a row are cached:

* ``id``, ``filename``, ``uploaded_by``, ``checksum``.

``scan_status`` and ``deleted`` are deliberately NOT cached: they are mutated
by the scanner and by deletion paths, and every serve gate re-reads them. A
cached entry is therefore never authoritative for "is this file servable" --
it only answers "which file is this and who owns it".

The cache is bounded by ``maxsize`` entries, thread-safe, and invalidated by
the manager on upload and on every deletion path (soft delete, permanent
delete, user erase, infected-file quarantine).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FileMeta:
    """Stable identity metadata for one ``media_files`` row."""

    id: int
    filename: str
    uploaded_by: int
    checksum: Optional[str] = None


class FileMetaCache:
    """Thread-safe bounded LRU keyed by file id and by filename."""

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = max(1, int(maxsize))
        # Each FileMeta is stored under two keys; the entry cap counts keys,
        # so the number of retained files is at most ``maxsize // 2``.
        self._entries: "OrderedDict[tuple, FileMeta]" = OrderedDict()
        self._lock = threading.Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "puts": 0,
            "evictions": 0,
            "invalidations": 0,
        }

    def get_by_id(self, file_id: int) -> Optional[FileMeta]:
        return self._get(("id", int(file_id)))

    def get_by_filename(self, filename: str) -> Optional[FileMeta]:
        return self._get(("filename", str(filename)))

    def _get(self, key: tuple) -> Optional[FileMeta]:
        with self._lock:
            meta = self._entries.get(key)
            if meta is None:
                self._stats["misses"] += 1
                return None
            self._entries.move_to_end(key)
            self._stats["hits"] += 1
            return meta

    def put(self, meta: FileMeta) -> None:
        """Insert ``meta`` under both lookup keys, evicting the LRU tail."""
        keys = (("id", int(meta.id)), ("filename", str(meta.filename)))
        with self._lock:
            for key in keys:
                self._entries.pop(key, None)
                self._entries[key] = meta
                self._stats["puts"] += 1
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
                self._stats["evictions"] += 1

    def invalidate(
        self, file_id: Optional[int] = None, filename: Optional[str] = None
    ) -> int:
        """Drop every entry matching ``file_id`` or ``filename``.

        A linear scan keeps this correct even if LRU eviction previously
        removed one of a file's two keys; with a bounded cache the scan is
        trivially cheap. Returns the number of removed keys.
        """
        want_id = None if file_id is None else int(file_id)
        want_name = None if filename is None else str(filename)
        removed = 0
        with self._lock:
            for key, meta in list(self._entries.items()):
                if (want_id is not None and int(meta.id) == want_id) or (
                    want_name is not None and str(meta.filename) == want_name
                ):
                    self._entries.pop(key, None)
                    removed += 1
            self._stats["invalidations"] += removed
        return removed

    def clear(self) -> None:
        """Drop all entries; counters are retained for observability."""
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


__all__ = ["FileMeta", "FileMetaCache"]
