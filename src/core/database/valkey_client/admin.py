import time
from typing import Any, Dict, List

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError


class AdminMixin(ValkeyClientBase):
    """Health and keyspace administration, blocking and awaitable."""

    def ping(self) -> bool:
        if not self._connected or not self._client:
            return False

        try:
            self._client.ping()
            return True
        except Exception:
            return False

    async def aping(self) -> bool:
        if not self._aconnected or not self._aclient:
            return False

        try:
            await self._aclient.ping()
            return True
        except Exception:
            return False

    def health_check(self) -> Dict[str, Any]:
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

    async def ahealth_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "enabled": self.enabled,
            "connected": self._aconnected,
            "responsive": False,
            "host": self.host,
            "port": self.port,
            "latency_ms": None,
        }

        if not self.enabled or not self._aconnected:
            return result

        try:
            start = time.time()
            await self._aclient.ping()
            latency = (time.time() - start) * 1000
            result["responsive"] = True
            result["latency_ms"] = round(latency, 2)
        except Exception as e:
            result["error"] = str(e)

        return result

    def flush_prefix(self) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None

        try:
            pattern = f"{self.key_prefix}*"
            raw_keys = self._scan_keys(client, pattern)
            keys = self._decode_list(raw_keys) if raw_keys else []
            if keys:
                return int(client.delete(keys))
            return 0
        except Exception as e:
            logger.error(f"GLIDE flush_prefix failed: {e}")
            raise ValkeyOperationError(f"flush_prefix failed: {e}")

    async def aflush_prefix(self) -> int:
        aclient = self._ensure_aconnected()

        try:
            pattern = f"{self.key_prefix}*"
            raw_keys = await self._ascan_keys(aclient, pattern)
            keys = self._decode_list(raw_keys) if raw_keys else []
            if keys:
                return int(await aclient.delete(keys))
            return 0
        except Exception as e:
            logger.error(f"GLIDE flush_prefix (async) failed: {e}")
            raise ValkeyOperationError(f"flush_prefix failed: {e}")

    def keys(self, pattern: str = "*") -> List[str]:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_pattern = self._prefixed_key(pattern)

        try:
            raw_keys = self._scan_keys(client, full_pattern)
            return self._strip_prefix(raw_keys)
        except Exception as e:
            logger.error(f"GLIDE KEYS (SCAN) failed for {pattern}: {e}")
            raise ValkeyOperationError(f"SCAN failed: {e}")

    async def akeys(self, pattern: str = "*") -> List[str]:
        aclient = self._ensure_aconnected()
        full_pattern = self._prefixed_key(pattern)

        try:
            raw_keys = await self._ascan_keys(aclient, full_pattern)
            return self._strip_prefix(raw_keys)
        except Exception as e:
            logger.error(f"GLIDE KEYS (SCAN, async) failed for {pattern}: {e}")
            raise ValkeyOperationError(f"SCAN failed: {e}")

    def _strip_prefix(self, raw_keys: List[bytes]) -> List[str]:
        prefix_len = len(self.key_prefix)
        decoded = self._decode_list(raw_keys) if raw_keys else []
        return [k[prefix_len:] if k.startswith(self.key_prefix) else k for k in decoded]

    @staticmethod
    def _scan_keys(client: Any, pattern: str) -> List[bytes]:
        keys: List[bytes] = []
        cursor: object = b"0"
        while True:
            result = client.scan(cursor, match=pattern, count=100)
            cursor = result[0]
            batch = result[1]
            if batch:
                keys.extend(batch)
            if cursor == b"0" or cursor == "0":
                break
        return keys

    @staticmethod
    async def _ascan_keys(client: Any, pattern: str) -> List[bytes]:
        """Async counterpart of :meth:`_scan_keys`.

        SCAN is cursor-based, so this is a sequence of round trips; each
        one awaits rather than blocking the loop for the whole walk.
        """
        keys: List[bytes] = []
        cursor: object = b"0"
        while True:
            result = await client.scan(cursor, match=pattern, count=100)
            cursor = result[0]
            batch = result[1]
            if batch:
                keys.extend(batch)
            if cursor == b"0" or cursor == "0":
                break
        return keys
