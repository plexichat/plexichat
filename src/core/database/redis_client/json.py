"""JSON data operations mixin."""

import json
from typing import Optional, Union

import utils.logger as logger

from .base import RedisClientBase, EnhancedJSONEncoder, RedisOperationError

JsonSerializable = Union[dict, list, str, int, float, bool, None, object]


class JSONMixin(RedisClientBase):
    """Mixin providing JSON serialization/deserialization operations."""

    def set_json(
        self, key: str, value: JsonSerializable, ttl: Optional[int] = None
    ) -> bool:
        """
        Store a JSON-serializable value.

        Args:
            key: The key name.
            value: Dict, list, or other JSON-serializable value.
            ttl: Time-to-live in seconds (optional).
        """
        try:
            json_str = json.dumps(value, separators=(",", ":"), cls=EnhancedJSONEncoder)
            return self.set(key, json_str, ttl)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON serialization failed for {key}: {e}")
            raise RedisOperationError(f"JSON serialization failed: {e}")

    def get_json(self, key: str) -> Optional[JsonSerializable]:
        """
        Get and deserialize a JSON value.

        Args:
            key: The key name.

        Returns:
            Deserialized value or None if not found.
        """
        value = self.get(key)
        if value is None:
            return None

        try:
            return json.loads(value)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON deserialization failed for {key}: {e}")
            raise RedisOperationError(f"JSON deserialization failed: {e}")
