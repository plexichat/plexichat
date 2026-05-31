"""
Session cache helpers.

Provides cache_session, get_cached_session, invalidate_session,
invalidate_user_sessions, and server cache invalidation helpers.
"""

import time
from typing import Any, Dict, Optional

import utils.logger as logger
from ..redis_client import (
    get_client,
    is_available,
    RedisOperationError,
)
from .operations import cache_get, cache_delete, invalidate_pattern


def cache_session(
    session_id: str, user_id: int, data: Dict[str, Any], ttl: Optional[int] = None
) -> bool:
    """Cache a user session."""
    client = get_client()
    if not client or not is_available():
        return False

    session_data: Dict[str, Any] = {
        "user_id": user_id,
        "created_at": time.time(),
        **data,
    }

    try:
        session_ttl = ttl if ttl is not None else client.ttl_session
        from .operations import cache_set as _cache_set

        _cache_set(f"session:{session_id}", session_data)
        # Set TTL separately since cache_set uses default TTL
        client.expire(f"session:{session_id}", session_ttl)
        client.sadd(f"user_sessions:{user_id}", session_id)
        logger.debug(f"Session cached: {session_id} for user {user_id}")
        return True
    except RedisOperationError as e:
        logger.warning(f"Failed to cache session {session_id}: {e}")
        return False


def get_cached_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get a cached session."""
    return cache_get(f"session:{session_id}")


def invalidate_session(session_id: str, user_id: Optional[int] = None) -> bool:
    """Invalidate a session."""
    client = get_client()
    if not client or not is_available():
        return False

    try:
        cache_delete(f"session:{session_id}")
        if user_id:
            client.srem(f"user_sessions:{user_id}", session_id)
        logger.debug(f"Session invalidated: {session_id}")
        return True
    except RedisOperationError as e:
        logger.warning(f"Failed to invalidate session {session_id}: {e}")
        return False


def invalidate_user_sessions(user_id: int) -> int:
    """Invalidate all sessions for a user."""
    client = get_client()
    if not client or not is_available():
        return 0

    try:
        session_ids = client.smembers(f"user_sessions:{user_id}")
        if session_ids:
            keys_to_delete = [f"session:{sid}" for sid in session_ids]
            keys_to_delete.append(f"user_sessions:{user_id}")
            client.delete(*keys_to_delete)
            logger.debug(f"Invalidated {len(session_ids)} sessions for user {user_id}")
            return len(session_ids)
        return 0
    except RedisOperationError as e:
        logger.warning(f"Failed to invalidate sessions for user {user_id}: {e}")
        return 0


def invalidate_user_servers(user_id: int) -> int:
    """Invalidate the server list cache for a user."""
    return invalidate_pattern(f"servers:*:{user_id}*")


def invalidate_server(server_id: int) -> int:
    """Invalidate the server list cache."""
    return invalidate_pattern("servers:*")


def invalidate_server_channels(server_id: int) -> int:
    """Invalidate the channel list cache for all users of a server."""
    return invalidate_pattern(f"channels:*:{server_id}*")
