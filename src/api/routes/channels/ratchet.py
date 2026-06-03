"""
API route: GET /api/channels/{channel_id}/ratchet

Returns the active ratchet interval for a channel, including the
wrapped start key, so that an authenticated client can decrypt
historical channel content offline.

The endpoint is gated by the license feature
``channel_ratchet_encryption``. Callers without the feature receive
``404``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.utils.encryption.channel_ratchet import (
    ChannelRatchetManager,
    RatchetInterval,
)
from src.utils.common_utils.utils.licensing import has_feature


LICENSE_FEATURE = "channel_ratchet_encryption"


def _feature_enabled() -> bool:
    try:
        return bool(has_feature(LICENSE_FEATURE))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"License check for {LICENSE_FEATURE} failed: {exc}")
        return False


def _resolve_manager(db: Any) -> Optional[ChannelRatchetManager]:
    try:
        if db is None:
            return None
        try:
            is_connected = bool(getattr(db, "is_connected", lambda: False)())
        except Exception:
            is_connected = False
        if not is_connected:
            return None
        if not db.table_exists("channel_ratchet_intervals"):
            return None
        return ChannelRatchetManager(db)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Failed to build ChannelRatchetManager: {exc}")
        return None


def _interval_to_response(interval: RatchetInterval) -> Dict[str, Any]:
    return interval.to_dict()


async def get_channel_ratchet(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the active ratchet interval for a channel.

    The response is a JSON-safe dict with the interval id, the
    start/end message ids, the base64-wrapped ``start_key``, and
    the context tag used for HKDF info construction.
    """
    if not _feature_enabled():
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": 404,
                    "message": "Channel ratchet is not enabled on this server",
                }
            },
        )

    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )

    messaging = api.get_messaging()
    servers_mod = api.get_servers()
    if messaging is None and servers_mod is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": 500,
                    "message": "Messaging / servers module not available",
                }
            },
        )

    has_access = False
    try:
        if servers_mod is not None and hasattr(servers_mod, "user_can_access_channel"):
            has_access = bool(
                servers_mod.user_can_access_channel(cid, current_user.user_id)
            )
    except Exception as exc:
        logger.debug(f"channel access probe failed: {exc}")

    if not has_access and messaging is not None:
        try:
            conv = messaging.get_conversation(cid, current_user.user_id)
            if conv is not None:
                has_access = True
        except Exception as exc:
            logger.debug(f"conversation probe failed: {exc}")

    if not has_access:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )

    try:
        db = api.get_db() if hasattr(api, "get_db") else None
    except Exception:
        db = None

    manager = _resolve_manager(db)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": 503,
                    "message": "Channel ratchet is not yet initialised",
                }
            },
        )

    snapshot = manager.snapshot(cid)
    if snapshot is None:
        return {
            "channel_id": cid,
            "interval": None,
            "enabled": True,
        }
    return {
        "channel_id": cid,
        "interval": snapshot,
        "enabled": True,
    }


def register_ratchet_routes(router: APIRouter) -> None:
    """Attach the ratchet endpoint to an existing channels router."""
    router.add_api_route(
        "/{channel_id}/ratchet",
        get_channel_ratchet,
        methods=["GET"],
        summary="Get active ratchet interval for a channel",
        responses={
            400: {"model": ErrorResponse, "description": "Invalid channel ID"},
            401: {"model": ErrorResponse, "description": "Not authenticated"},
            403: {"model": ErrorResponse, "description": "Access denied"},
            404: {
                "model": ErrorResponse,
                "description": "Channel ratchet not enabled or not found",
            },
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
