"""
Presence cache helpers.

Provides cache_presence, get_cached_presence, and get_bulk_presence functions.
"""

import time
from typing import Any, Dict, List, Optional

import utils.logger as logger
from .base import default_ttl
from .operations import cache_get, cache_set, cache_get_many


def cache_presence(
    user_id: int, status: str, custom_status: Optional[str] = None
) -> bool:
    """Cache user presence/status.

    NOTE: this helper writes a minimal, standalone presence snapshot. The
    full presence schema (see ``PresenceManager._presence_to_dict``) includes
    ``user_id``; omitting it here made ``get_presence`` raise ``KeyError``
    when it tried to hydrate the cached entry. ``user_id`` is now included so
    the entry is always consumable by ``_dict_to_presence``.
    """
    presence_data: Dict[str, Any] = {
        "user_id": user_id,
        "status": status,
        "custom_status": custom_status,
        "activity": None,
        "last_seen": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
        "current_channel_id": None,
        "current_server_id": None,
    }

    try:
        return cache_set(
            f"presence:{user_id}", presence_data, ttl=default_ttl("presence")
        )
    except Exception as e:
        logger.warning(f"Failed to cache presence for user {user_id}: {e}")
        return False


def get_cached_presence(user_id: int) -> Optional[Dict[str, Any]]:
    """Get cached user presence."""
    return cache_get(f"presence:{user_id}")


def get_bulk_presence(user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Get presence for multiple users in a single round-trip when Valkey is
    available (MGET), otherwise the in-memory fallback."""
    if not user_ids:
        return {}
    cached = cache_get_many([f"presence:{uid}" for uid in user_ids])
    result: Dict[int, Dict[str, Any]] = {}
    for key, data in cached.items():
        if not isinstance(data, dict):
            continue
        try:
            uid = int(key.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            continue
        result[uid] = data
    return result
