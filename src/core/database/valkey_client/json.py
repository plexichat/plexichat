import json
from typing import Optional, Union

import utils.logger as logger

from .base import ValkeyClientBase, EnhancedJSONEncoder, ValkeyOperationError

JsonSerializable = Union[dict, list, str, int, float, bool, None, object]


class JSONMixin(ValkeyClientBase):
    def set_json(
        self, key: str, value: JsonSerializable, ttl: Optional[int] = None
    ) -> bool:
        try:
            json_str = json.dumps(value, separators=(",", ":"), cls=EnhancedJSONEncoder)
            return self.set(key, json_str, ttl)
        except (TypeError, ValueError) as e:
            logger.error(f"JSON serialization failed for {key}: {e}")
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
