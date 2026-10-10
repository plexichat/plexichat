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
    "asetup",
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


async def asetup() -> Optional[ValkeyClient]:
    """Open the event-loop handle on the process-wide client.

    Separate from :func:`setup` because a coroutine handle belongs to the
    loop that creates it: this must be awaited from the serving loop, and
    the sync handle opened by :func:`setup` cannot be reused for it.

    Idempotent -- calling it when the handle is already open is a no-op.
    Returns ``None`` when Valkey is disabled or unreachable, matching
    :func:`setup`'s graceful-degradation contract.
    """
    global _default_client
    if _default_client is None:
        _default_client = ValkeyClient()

    if not _default_client.enabled:
        return None

    if _default_client._aconnected:
        return _default_client

    try:
        await _default_client.aconnect()
        return _default_client
    except ValkeyConnectionError as e:
        logger.warning(f"GLIDE async setup failed, continuing without it: {e}")
        return None


def get_client() -> Optional[ValkeyClient]:
    return _default_client


def is_available() -> bool:
    """True when either handle can be used.

    Callers gating an operation on availability must see the async-only
    case too: a deployment whose event loop opened the async handle but
    never the sync one is fully usable on the async paths.
    """
    if _default_client is None:
        return False
    return _default_client._connected or _default_client._aconnected
