from typing import Optional, Union

import utils.logger as logger

from .base import ValkeyError, ValkeyConnectionError, ValkeyOperationError
from .composer import ValkeyClient

JsonSerializable = Union[dict, list, str, int, float, bool, None, object]

__all__ = [
    "ValkeyClient",
    "ValkeyError",
    "ValkeyConnectionError",
    "ValkeyOperationError",
    "JsonSerializable",
    "setup",
    "get_client",
    "is_available",
]

_default_client: Optional[ValkeyClient] = None


def setup() -> Optional[ValkeyClient]:
    global _default_client
    _default_client = ValkeyClient()

    if _default_client.enabled:
        try:
            _default_client.connect()
            return _default_client
        except ValkeyConnectionError as e:
            logger.warning(f"GLIDE setup failed, continuing without GLIDE: {e}")
            return None
    return None


def get_client() -> Optional[ValkeyClient]:
    return _default_client


def is_available() -> bool:
    return _default_client is not None and _default_client._connected
