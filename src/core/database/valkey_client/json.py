import json
from typing import Dict, List, Optional, Union

import utils.logger as logger

from .base import ValkeyClientBase, EnhancedJSONEncoder, ValkeyOperationError

JsonSerializable = Union[dict, list, str, int, float, bool, None, object]


class JSONMixin(ValkeyClientBase):
    """JSON values, in blocking and awaitable form."""

    def set_json(
        self, key: str, value: JsonSerializable, ttl: Optional[int] = None
    ) -> bool:
        try:
            json_str = json.dumps(value, separators=(",", ":"), cls=EnhancedJSONEncoder)
            return self.set(key, json_str, ttl)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON serialization failed for {key}: {e}")
            raise ValkeyOperationError(f"JSON serialization failed: {e}")

    async def aset_json(
        self, key: str, value: JsonSerializable, ttl: Optional[int] = None
    ) -> bool:
        try:
            json_str = json.dumps(value, separators=(",", ":"), cls=EnhancedJSONEncoder)
            return await self.aset(key, json_str, ttl)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON serialization (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"JSON serialization failed: {e}")

    def get_json(self, key: str) -> Optional[JsonSerializable]:
        value = self.get(key)
        if value is None:
            return None

        try:
            return json.loads(value)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON deserialization failed for {key}: {e}")
            raise ValkeyOperationError(f"JSON deserialization failed: {e}")

    async def aget_json(self, key: str) -> Optional[JsonSerializable]:
        value = await self.aget(key)
        if value is None:
            return None

        try:
            return json.loads(value)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON deserialization (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"JSON deserialization failed: {e}")

    def mget_json(self, keys: List[str]) -> Dict[str, JsonSerializable]:
        """Fetch and decode multiple JSON values in one Valkey round trip."""
        if not keys:
            return {}
        self._ensure_connected()
        client = self._client
        if client is None:
            raise ValkeyOperationError("GLIDE client is not connected")

        full_keys = [self._prefixed_key(self._sanitize_key(key)) for key in keys]
        try:
            values = client.mget(full_keys)
            return self._decode_json_mget(keys, values)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON bulk deserialization failed: {e}")
            raise ValkeyOperationError(f"JSON bulk deserialization failed: {e}")
        except Exception as e:
            logger.error(f"GLIDE MGET failed: {e}")
            raise ValkeyOperationError(f"MGET failed: {e}")

    async def amget_json(self, keys: List[str]) -> Dict[str, JsonSerializable]:
        """Async counterpart of :meth:`mget_json` -- one round trip."""
        if not keys:
            return {}
        aclient = self._ensure_aconnected()

        full_keys = [self._prefixed_key(self._sanitize_key(key)) for key in keys]
        try:
            values = await aclient.mget(full_keys)
            return self._decode_json_mget(keys, values)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON bulk deserialization (async) failed: {e}")
            raise ValkeyOperationError(f"JSON bulk deserialization failed: {e}")
        except Exception as e:
            logger.error(f"GLIDE MGET (async) failed: {e}")
            raise ValkeyOperationError(f"MGET failed: {e}")

    def _decode_json_mget(
        self, keys: List[str], values: List[Optional[bytes]]
    ) -> Dict[str, JsonSerializable]:
        decoded = self._decode_mget(values)
        result: Dict[str, JsonSerializable] = {}
        for key, value in zip(keys, decoded):
            if value is not None:
                result[key] = json.loads(value)
        return result
