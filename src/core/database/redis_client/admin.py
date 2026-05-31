"""Admin and monitoring operations mixin."""

import time
from typing import Any, Dict, List, cast

import utils.logger as logger

from .base import RedisClientBase, RedisOperationError


class AdminMixin(RedisClientBase):
    """Mixin providing admin/monitoring operations."""

    def ping(self) -> bool:
        """Check if Redis is responsive."""
        if not self._connected or not self._client:
            return False

        try:
            return self._client.ping()
        except Exception:
            return False

    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on Redis connection.

        Returns:
            Dict with health status information.
        """
        result = {
            "enabled": self.enabled,
            "connected": self._connected,
            "responsive": False,
            "host": self.host,
            "port": self.port,
            "latency_ms": None,
        }

        if not self.enabled or not self._connected:
            return result

        try:
            start = time.time()
            client = self._client
            assert client is not None
            client.ping()
            latency = (time.time() - start) * 1000
            result["responsive"] = True
            result["latency_ms"] = round(latency, 2)
        except Exception as e:
            result["error"] = str(e)

        return result

    def flush_prefix(self) -> int:
        """
        Delete all keys with the configured prefix.
        Use with caution!

        Returns:
            Number of keys deleted.
        """
        self._ensure_connected()
        client = self._client
        assert client is not None

        try:
            pattern = f"{self.key_prefix}*"
            keys = cast(List[str], client.keys(pattern))
            if keys:
                return int(client.delete(*keys))
            return 0
        except Exception as e:
            logger.error(f"Redis flush_prefix failed: {e}")
            raise RedisOperationError(f"flush_prefix failed: {e}")

    def keys(self, pattern: str = "*") -> List[str]:
        """
        Get keys matching a pattern using non-blocking SCAN.

        Args:
            pattern: Glob-style pattern (e.g., "user:*").

        Returns:
            List of matching keys (without prefix).
        """
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_pattern = self._prefixed_key(pattern)

        try:
            keys = []
            for k in client.scan_iter(match=full_pattern, count=100):
                keys.append(k)

            prefix_len = len(self.key_prefix)
            return [
                k[prefix_len:] if k.startswith(self.key_prefix) else k for k in keys
            ]
        except Exception as e:
            logger.error(f"Redis KEYS (SCAN) failed for {pattern}: {e}")
            raise RedisOperationError(f"SCAN failed: {e}")
