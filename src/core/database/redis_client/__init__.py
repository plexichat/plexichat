"""Redis client package - Provides Redis connectivity for caching, sessions, and pub/sub.

This module follows the zero-friction pattern established by common_utils.
It requires config and logger to be set up before use.

Features:
    - Connection pooling with automatic reconnection
    - TLS/SSL support for secure connections
    - Key prefixing to avoid collisions
    - Graceful degradation when Redis is unavailable
    - Pub/Sub support for real-time events
    - Health checks and connection monitoring
"""

from typing import Optional, Union

import utils.logger as logger

from .base import RedisError, RedisConnectionError, RedisOperationError
from .composer import RedisClient

JsonSerializable = Union[dict, list, str, int, float, bool, None, object]

__all__ = [
    "RedisClient",
    "RedisError",
    "RedisConnectionError",
    "RedisOperationError",
    "JsonSerializable",
    "setup",
    "get_client",
    "is_available",
]

# ==================== Module-level convenience functions ====================

_default_client: Optional[RedisClient] = None


def setup() -> Optional[RedisClient]:
    """
    Setup the default Redis client.

    Returns:
        RedisClient instance or None if disabled/failed.
    """
    global _default_client
    _default_client = RedisClient()

    if _default_client.enabled:
        try:
            _default_client.connect()
            return _default_client
        except RedisConnectionError as e:
            logger.warning(f"Redis setup failed, continuing without Redis: {e}")
            return None
    return None


def get_client() -> Optional[RedisClient]:
    """Get the default Redis client."""
    return _default_client


def is_available() -> bool:
    """Check if Redis is available and connected."""
    return _default_client is not None and _default_client._connected
