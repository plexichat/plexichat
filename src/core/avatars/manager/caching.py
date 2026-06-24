"""Caching mixin for the avatars module."""

import threading
import time
from collections import OrderedDict
from typing import Optional

import utils.logger as logger

from .protocol import AvatarProtocol


class AvatarCachingMixin(AvatarProtocol):
    """Mixin handling Redis caching of avatar data.

    BOUNDED: a per-process LRU caps the active cache key set so a
    long-running Plexichat instance with high avatar churn cannot
    leak entries indefinitely while Redis TTL is the only filter.
    The 1024-entry ceiling is sized for a single container; tune via
    ``_AVATAR_CACHE_MAX`` for higher-density deployments.
    """

    _AVATAR_CACHE_MAX = 1024
    _AVATAR_CACHE_TTL_SEC = 600  # 10 minutes

    def __init__(self, db=None) -> None:
        # AvatarProtocol has no __init__; object.__init__ takes 0 args.
        # Stash db for downstream consumers; the mixin-only state follows.
        self._db = db
        # Track ONLY the in-process key set so we can prune aggressively
        # on churn. Values are (bytes, last_used_ts).
        self._key_set: "OrderedDict[str, float]" = OrderedDict()
        self._key_lock = threading.Lock()

    def _cache_binary(self, key: str, data: bytes, ttl: int = 3600) -> None:
        """Cache binary data in Redis.

        TRACK the key in the bounded in-process set so we can later
        prune if the LRU is full.  We do NOT keep the data here —
        Redis owns the bytes; the in-process set is only a keydir.
        """
        from src.core.database import get_redis_client, redis_available

        if not redis_available():
            return
        try:
            client = get_redis_client()
            if client:
                client.set_bin(key, data, ttl=ttl)
                with self._key_lock:
                    self._key_set[key] = time.monotonic()
                    if len(self._key_set) > self._AVATAR_CACHE_MAX:
                        # drop oldest by insertion order
                        evicted = self._key_set.popitem(last=False)
                        logger.debug(
                            f"Avatar cache keydir at cap, evicted {evicted[0]}"
                        )
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
