"""
Presence cache helpers.

Provides cache_presence, get_cached_presence, and get_bulk_presence functions.
"""

import time
from typing import Any, Dict, List, Optional

import utils.logger as logger
from ..redis_client import (
    get_client,
    is_available,
    RedisOperationError,
)
from .operations import cache_get


def cache_presence(
    user_id: int, status: str, custom_status: Optional[str] = None
) -> bool:
    """Cache user presence/status."""
    client = get_client()
    if not client or not is_available():
        return False

    presence_data: Dict[str, Any] = {
        "status": status,
        "custom_status": custom_status,
        "updated_at": time.time(),
    }

    try:
        client.set_json(f"presence:{user_id}", presence_data, ttl=client.ttl_presence)
        return True
    except RedisOperationError as e:
        logger.warning(f"Failed to cache presence for user {user_id}: {e}")
        return False


def get_cached_presence(user_id: int) -> Optional[Dict[str, Any]]:
    """Get cached user presence."""
    return cache_get(f"presence:{user_id}")


def get_bulk_presence(user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Get presence for multiple users."""
    result: Dict[int, Dict[str, Any]] = {}
    for user_id in user_ids:
        presence = get_cached_presence(user_id)
        if presence:
            result[user_id] = presence
    return result
