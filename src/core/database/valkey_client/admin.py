import time
from typing import Any, Dict, List

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError


class AdminMixin(ValkeyClientBase):
    def ping(self) -> bool:
        if not self._connected or not self._client:
            return False

        try:
            self._client.ping()
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

    def keys(self, pattern: str = "*") -> List[str]:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_pattern = self._prefixed_key(pattern)

        try:
            raw_keys = self._scan_keys(client, full_pattern)
            prefix_len = len(self.key_prefix)
            decoded = self._decode_list(raw_keys) if raw_keys else []
            return [
                k[prefix_len:] if k.startswith(self.key_prefix) else k for k in decoded
            ]
        except Exception as e:
            logger.error(f"GLIDE KEYS (SCAN) failed for {pattern}: {e}")
            raise ValkeyOperationError(f"SCAN failed: {e}")

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
