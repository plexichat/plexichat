"""Per-conversation generation counters used to invalidate render caches.

The REST message list keeps a small in-process LRU of fully normalized render
payloads. A cached page must become stale the instant any message in its
conversation is created, edited or deleted. The messaging layer owns that
event (``MessageRepository.invalidate_conversation_cache``), so it bumps the
conversation's generation here; the API layer folds the generation into its
cache key. Keeping the counter in this neutral module avoids an import cycle
between core messaging and the API routes.
"""

from threading import Lock
from typing import Any, Dict

_lock = Lock()
_generations: Dict[Any, int] = {}


def _normalize(conversation_id: Any) -> Any:
    try:
        return int(conversation_id)
    except (TypeError, ValueError):
        return conversation_id


def bump(conversation_id: Any) -> int:
    """Advance and return the generation for ``conversation_id``."""
    key = _normalize(conversation_id)
    with _lock:
        value = _generations.get(key, 0) + 1
        _generations[key] = value
        return value


def get(conversation_id: Any) -> int:
    """Return the current generation (0 when the conversation was never touched)."""
    key = _normalize(conversation_id)
    with _lock:
        return _generations.get(key, 0)


def clear() -> None:
    """Reset all counters. Test helper only."""
    with _lock:
        _generations.clear()
