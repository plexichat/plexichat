"""
API route: GET /api/channels/{channel_id}/ratchet

Returns the active ratchet interval for a channel, including the
wrapped start key, so that an authenticated client can decrypt
historical channel content offline.

The endpoint is a read-only state query and always returns ``200``
with the current ratchet state for the channel. The ``enabled`` field
in the response reflects whether the server is licensed for the
premium ``channel_ratchet_encryption`` feature; on an unlicensed
server the field is ``false`` and ``interval`` is ``None``. The
encryption write path (``send``/``edit``) is what the licence gates,
not this read.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.utils.encryption.channel_ratchet import ChannelRatchetManager
from src.utils.encryption.channel_ratchet.gate import ratchet_encryption_licensed

# Snowflake fields in the ratchet payload that must be serialized as strings.
_SNOWFLAKE_FIELDS = (
    "interval_id",
    "conversation_id",
    "start_message_id",
    "end_message_id",
)


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


def _stringify_ids(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with snowflake fields serialized as decimal strings.

    A JavaScript ``Number`` cannot represent a 64-bit snowflake exactly, so
    an ``interval_id`` served as a JSON number is rounded by the client and
    every derived ratchet key/AAD then mismatches the server. Emit these
    fields as strings (the project-wide snowflake convention) so the client
    derives keys from the exact id.
    """
    out = dict(payload)
    for field in _SNOWFLAKE_FIELDS:
        if out.get(field) is not None:
            out[field] = str(out[field])
    return out


def _parse_channel_id(channel_id: str) -> int:
    try:
        return int(channel_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )


def _require_channel_access(cid: int, user_id: int) -> None:
    """Raise ``HTTPException`` unless ``user_id`` can see the channel."""
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
        if servers_mod is not None and hasattr(servers_mod, "get_channel"):
            channel = servers_mod.get_channel(cid, user_id)
            if channel is not None:
                has_access = True
    except Exception as exc:
        logger.debug(f"channel access probe failed: {exc}")

    if not has_access and messaging is not None:
        try:
            conv = messaging.get_conversation(cid, user_id)
            if conv is not None:
                has_access = True
        except Exception as exc:
            logger.debug(f"conversation probe failed: {exc}")

    if not has_access:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )


def _resolve_db() -> Any:
    try:
        return api.get_db() if hasattr(api, "get_db") else None
    except Exception:
        return None


async def get_channel_ratchet(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the active ratchet interval for a channel.

    The response is a JSON-safe dict with the interval id, the
    start/end message ids, the base64-wrapped ``start_key``, and
    the context tag used for HKDF info construction. When the
    server is not licensed for ``channel_ratchet_encryption`` the
    response is still ``200`` with ``enabled: false`` and
    ``interval: None`` so clients can detect the feature flag
    without a separate config call.
    """
    cid = _parse_channel_id(channel_id)
    _require_channel_access(cid, current_user.user_id)

    db = _resolve_db()
    enabled = ratchet_encryption_licensed()
    manager = _resolve_manager(db)
    snapshot = manager.snapshot(cid) if manager is not None else None
    if snapshot is None:
        return {
            "channel_id": str(cid),
            "interval": None,
            "enabled": enabled,
        }
    return {
        "channel_id": str(cid),
        "interval": _stringify_ids(snapshot),
        "enabled": enabled,
    }


async def get_channel_ratchet_intervals(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return every ratchet interval for a channel, newest first.

    The client ratchet calls this when an envelope names an interval that
    is no longer active (a rotation it did not observe), so it can still
    resolve historical ciphertext. Each entry carries the same fields as
    the active-interval endpoint's ``interval``.
    """
    cid = _parse_channel_id(channel_id)
    _require_channel_access(cid, current_user.user_id)

    db = _resolve_db()
    enabled = ratchet_encryption_licensed()
    manager = _resolve_manager(db)
    intervals = manager.snapshot_list(cid) if manager is not None else []
    return {
        "channel_id": str(cid),
        "intervals": [_stringify_ids(item) for item in intervals],
        "enabled": enabled,
    }


def register_ratchet_routes(router: APIRouter) -> None:
    """Attach the ratchet endpoints to an existing channels router."""
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
    router.add_api_route(
        "/{channel_id}/ratchet/intervals",
        get_channel_ratchet_intervals,
        methods=["GET"],
        summary="List ratchet intervals for a channel",
        responses={
            400: {"model": ErrorResponse, "description": "Invalid channel ID"},
            401: {"model": ErrorResponse, "description": "Not authenticated"},
            403: {"model": ErrorResponse, "description": "Access denied"},
            404: {
                "model": ErrorResponse,
                "description": "Channel not found",
            },
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
