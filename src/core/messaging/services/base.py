"""
Base service with common functionality.
"""

from typing import Any, Optional
import time

from src.core.base import SnowflakeID
from src.core.database.collections import CappedDict
import utils.config as config


class BaseService:
    """Base class for all services."""

    def __init__(
        self,
        db: Any,
        config_section: str = "messaging",
        cache_max_size: Optional[int] = None,
    ) -> None:
        """
        Initialize service.

        Args:
            db: Database instance
            config_section: Config section name for this service
            cache_max_size: Maximum cache size (None uses config default)
        """
        self._db = db
        self._config = config.get(config_section, {})

        max_cache = cache_max_size or config.get("redis.cache_max_items", 1000)
        self._cache: CappedDict = CappedDict(max_size=max_cache)
        self._cache_ttl = 60.0  # 60 second cache TTL

        # Snowflake ID generation
        self._id_counter = 0
        self._last_timestamp = 0
        self._machine_id = config.get("machine_id", 1) & 0x3FF  # 10 bits

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> SnowflakeID:
        """Generate a unique Snowflake ID."""
        timestamp = self._get_timestamp()

        if timestamp == self._last_timestamp:
            self._id_counter = (self._id_counter + 1) & 0xFFF  # 12 bits
            if self._id_counter == 0:
                # Wait for next millisecond
                while timestamp <= self._last_timestamp:
                    timestamp = self._get_timestamp()
        else:
            self._id_counter = 0

        self._last_timestamp = timestamp

        # Snowflake format: timestamp (42 bits) | machine_id (10 bits) | sequence (12 bits)
        snowflake = ((timestamp - 1420070400000) << 22) | (self._machine_id << 12) | self._id_counter
        return snowflake

    def _cache_get(self, key: Any, default: Optional[Any] = None) -> Optional[Any]:
        """Get value from cache (Local memory first, then Redis)."""
        # 1. Try local memory
        if key in self._cache:
            value, expires = self._cache[key]
            if (self._get_timestamp() / 1000.0) < expires:
                return value
            del self._cache[key]

        # 2. Try Redis
        from src.core.database import cache_get, redis_available
        if redis_available():
            cache_key = f"msg_cache:{self.__class__.__name__}:{key}"
            redis_val = cache_get(cache_key)
            if redis_val is not None:
                # Store back in local memory for even faster subsequent access
                self._cache_set(key, redis_val)
                return redis_val

        return default

    def _cache_set(self, key: Any, value: Any) -> None:
        """Set value in cache (Local memory and Redis)."""
        self._cache[key] = (value, (self._get_timestamp() / 1000.0) + self._cache_ttl)
        
        from src.core.database import cache_set, redis_available
        if redis_available():
            cache_key = f"msg_cache:{self.__class__.__name__}:{key}"
            cache_set(cache_key, value, ttl=int(self._cache_ttl))

    def _cache_invalidate(self, key: Any) -> None:
        """Invalidate a cache entry (Local memory and Redis)."""
        self._cache.pop(key, None)
        
        from src.core.database import cache_delete, redis_available
        if redis_available():
            cache_key = f"msg_cache:{self.__class__.__name__}:{key}"
            cache_delete(cache_key)

    def _get_config(self, key: str, default: Any = None) -> Any:
        """Get config value with default."""
        return self._config.get(key, default)
