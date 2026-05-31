"""Caching mixin for the avatars module."""

from typing import Optional

import utils.logger as logger

from .protocol import AvatarProtocol


class AvatarCachingMixin(AvatarProtocol):
    """Mixin handling Redis caching of avatar data."""

    def _cache_binary(self, key: str, data: bytes, ttl: int = 3600) -> None:
        """Cache binary data in Redis."""
        from src.core.database import get_redis_client, redis_available

        if not redis_available():
            return
        try:
            client = get_redis_client()
            if client:
                client.set_bin(key, data, ttl=ttl)
        except Exception as e:
            logger.debug(f"Failed to cache binary data for {key}: {e}")

    def _get_cached_binary(self, key: str) -> Optional[bytes]:
        """Get cached binary data from Redis."""
        from src.core.database import get_redis_client, redis_available

        if not redis_available():
            return None
        try:
            client = get_redis_client()
            if client:
                return client.get_bin(key)
        except Exception as e:
            logger.debug(f"Failed to get cached binary data for {key}: {e}")
        return None

    def _delete_cached_binary(self, key: str) -> None:
        """Delete cached binary data from Redis."""
        from src.core.database import get_redis_client, redis_available

        if not redis_available():
            return
        try:
            client = get_redis_client()
            if client:
                client.delete(key)
        except Exception as e:
            logger.debug(f"Failed to delete cached binary data for {key}: {e}")
