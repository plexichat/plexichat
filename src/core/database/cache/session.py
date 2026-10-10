"""
Session cache helpers.

Provides cache_session, get_cached_session, invalidate_session,
invalidate_user_sessions, and server cache invalidation helpers.
"""

import time
from typing import Any, Dict, Optional

import utils.logger as logger
from ..valkey_client import (
    get_client,
    is_available,
    ValkeyOperationError,
)
from .operations import cache_get, cache_delete, cache_set, invalidate_pattern
from .base import default_ttl, mem_cache_delete, mem_cache_get, mem_cache_matching_keys


def cache_session(
    session_id: str, user_id: int, data: Dict[str, Any], ttl: Optional[int] = None
) -> bool:
    """Cache a user session.

    Works with Valkey when connected (also tracking the user's session set for
    bulk invalidation); otherwise falls back to the in-memory tier so the
    session is still served from cache in single-node deployments.
    """
    session_data: Dict[str, Any] = {
        "user_id": user_id,
        "created_at": time.time(),
        **data,
    }

    try:
        session_ttl = ttl if ttl is not None else default_ttl("session")
        stored = cache_set(f"session:{session_id}", session_data, ttl=session_ttl)
        client = get_client()
        if client and is_available():
            client.sadd(f"user_sessions:{user_id}", session_id)
        logger.debug(f"Session cached: {session_id} for user {user_id}")
        return stored
    except ValkeyOperationError as e:
        logger.warning(f"Failed to cache session {session_id}: {e}")
        return False


def get_cached_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get a cached session."""
    return cache_get(f"session:{session_id}")


def invalidate_session(session_id: str, user_id: Optional[int] = None) -> bool:
    """Invalidate a session."""
    try:
        cache_delete(f"session:{session_id}")
        client = get_client()
        if user_id and client and is_available():
            client.srem(f"user_sessions:{user_id}", session_id)
        logger.debug(f"Session invalidated: {session_id}")
        return True
    except ValkeyOperationError as e:
        logger.warning(f"Failed to invalidate session {session_id}: {e}")
        return False


def invalidate_user_sessions(user_id: int) -> int:
    """Invalidate all sessions for a user.

    Clears BOTH cache tiers: the Valkey ``user_sessions`` set (when Valkey is
    available) AND the in-memory fallback entries. Previously this was a
    silent no-op whenever Valkey was down, even though ``cache_session``
    keeps serving sessions from the in-memory tier — "logout everywhere"
    deleted nothing and stale sessions stayed valid until TTL expiry.
    """
    client = get_client()
    invalidated = 0

    if client and is_available():
        try:
            session_ids = list(client.smembers(f"user_sessions:{user_id}"))
            if session_ids:
                keys_to_delete = [f"session:{sid}" for sid in session_ids]
                keys_to_delete.append(f"user_sessions:{user_id}")
                for key in keys_to_delete:
                    cache_delete(key)
                invalidated += len(session_ids)
                logger.debug(
                    f"Invalidated {len(session_ids)} Valkey-cached sessions "
                    f"for user {user_id}"
                )
        except ValkeyOperationError as e:
            logger.warning(f"Failed to invalidate sessions for user {user_id}: {e}")

    invalidated += _invalidate_mem_user_sessions(user_id)

    if invalidated == 0:
        logger.debug(f"No cached sessions found for user {user_id}")
    return invalidated


def _invalidate_mem_user_sessions(user_id: int) -> int:
    """Clear the user's session entries from the in-memory fallback tier.

    Returns the number of memory-tier sessions invalidated.
    """
    count = 0
    for key in mem_cache_matching_keys("session:*"):
        entry = mem_cache_get(key)
        if not isinstance(entry, dict) or entry.get("user_id") != user_id:
            continue
        if mem_cache_delete(key):
            count += 1
            # Best-effort: drop the membership record from Valkey too, in
            # case this session was tracked there while its payload only
            # ever lived in memory.
            client = get_client()
            if client and is_available():
                try:
                    client.srem(f"user_sessions:{user_id}", str(key)[len("session:") :])
                except ValkeyOperationError:
                    pass
    return count


def invalidate_user_servers(user_id: int) -> int:
    """Invalidate the server list cache for a user."""
    return invalidate_pattern(f"servers:*:{user_id}*")


def invalidate_server(server_id: int) -> int:
    """Invalidate the server list cache."""
    return invalidate_pattern("servers:*")


def invalidate_server_channels(server_id: int) -> int:
    """Invalidate the channel list cache for all users of a server."""
    return invalidate_pattern(f"channels:*:{server_id}*")
