"""
Websocket bridge for the channel ratchet.

When a ratchet interval is rotated or split, connected clients
should drop any cached copy of the previous active-interval key
before the next message arrives. This module provides a
fire-and-forget helper that schedules a RATCHET_UPDATE broadcast
on the websocket gateway's running loop.

Failures are logged at debug level and otherwise swallowed: a
ratchet change that is missed by a client only causes the next
decrypt to fail (which the client already handles by refetching
the active interval), so the broadcast must never raise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def notify_ratchet_update(
    conversation_id: int,
    update_data: Dict[str, Any],
) -> None:
    """Schedule a fire-and-forget RATCHET_UPDATE websocket broadcast.

    Safe to call from sync code paths (e.g. a FastAPI route handler
    that called into a sync service method running on a threadpool).
    If no asyncio loop is running, the broadcast is dropped silently
    (clients will recover on the next failed decrypt by re-fetching
    the active interval).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug(
            "notify_ratchet_update: no running loop; dropping broadcast "
            "for conversation %s",
            conversation_id,
        )
        return

    try:
        from src.api.websocket import broadcast_ratchet_update

        asyncio.run_coroutine_threadsafe(
            broadcast_ratchet_update(conversation_id, update_data),
            loop,
        )
    except Exception as e:
        logger.debug(
            "notify_ratchet_update: broadcast scheduling failed for "
            "conversation %s: %s",
            conversation_id,
            e,
        )


async def notify_ratchet_update_async(
    conversation_id: int,
    update_data: Dict[str, Any],
) -> Optional[int]:
    """Awaitable variant of :func:`notify_ratchet_update`.

    Returns the number of connections notified, or ``None`` if the
    websocket module is not initialized.
    """
    try:
        from src.api.websocket import broadcast_ratchet_update
    except Exception as e:
        logger.debug("notify_ratchet_update_async: import failed: %s", e)
        return None

    try:
        return await broadcast_ratchet_update(conversation_id, update_data)
    except Exception as e:
        logger.debug(
            "notify_ratchet_update_async: broadcast failed for conversation %s: %s",
            conversation_id,
            e,
        )
        return None
