"""Bounded LRU cache for normalized message render payloads.

BEFORE: every message list request re-serialized the whole page (and re-ran
the batched enrichment queries) even when the same user repeatedly requested
the same page within the 10s API cache TTL, because that API cache lives in
Redis/Valkey and still costs a network round trip plus a cache lookup.

AFTER: a small, bounded, thread-safe in-process LRU serves repeated identical
pages without any I/O. Entries expire on a short TTL as a backstop and are
invalidated explicitly by folding the conversation's render generation into
every key (see ``src.core.messaging.render_generation``), so an edit/delete in
a conversation can never keep serving an old page.
"""

import time
from collections import OrderedDict
from threading import RLock
from typing import Any, Hashable, Optional

_DEFAULT_MAXSIZE = 256
_DEFAULT_TTL_SECONDS = 10.0


class MessageRenderCache:
    """A bounded, TTL'd, thread-safe LRU keyed by a hashable page descriptor."""

    def __init__(
        self, maxsize: int = _DEFAULT_MAXSIZE, ttl: float = _DEFAULT_TTL_SECONDS
    ):
        self._maxsize = max(1, int(maxsize))
        self._ttl = max(0.0, float(ttl))
        self._entries: "OrderedDict[Hashable, tuple]" = OrderedDict()
        self._lock = RLock()

    def get(self, key: Hashable) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: Hashable, value: Any) -> None:
        now = time.monotonic()
        with self._lock:
            self._entries[key] = (now + self._ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def remove_conversation(self, conversation_id: Any) -> None:
        """Drop every entry whose key embeds ``conversation_id`` (tests/ops)."""
        needle = str(conversation_id)
        with self._lock:
            for key in list(self._entries):
                if needle in "".join(str(part) for part in _as_tuple(key)):
                    del self._entries[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _as_tuple(key: Hashable) -> tuple:
    return key if isinstance(key, tuple) else (key,)


_RENDER_CACHE = MessageRenderCache()


def get_render_cache() -> MessageRenderCache:
    """Return the process-wide render cache."""
    return _RENDER_CACHE
