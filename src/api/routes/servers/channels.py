from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    ChannelResponse,
    ChannelCreateRequest,
    InviteResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse
from src.core.database import (
    invalidate_server_channels,
    cached,
)
from src.core.servers.models import ChannelType

import utils.logger as logger
from .helpers import _channel_to_response

router = APIRouter()


@router.get(
    "/{server_id}/channels",
    response_model=List[ChannelResponse],
    summary="List server channels",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="server_channels_api")
def get_server_channels(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[ChannelResponse]:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        if not servers_mod.server_exists(sid):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )

        channels = servers_mod.get_channels(current_user.user_id, sid)
        if channels is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch channels"}},
            )
        return [_channel_to_response(c) for c in channels]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Access" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(
            f"Failed to fetch channels for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/{server_id}/channels",
    response_model=ChannelResponse,
    summary="Create channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_server_channel(
    server_id: str,
    body: ChannelCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ChannelResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        sid = int(server_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server ID"}},
        )

    kwargs: Dict[str, Any] = {
        "user_id": current_user.user_id,
        "server_id": sid,
        "name": body.name,
    }
    type_val = getattr(body, "channel_type", None) or getattr(body, "type", None)
    if type_val:
        type_str = type_val.lower()
        try:
            kwargs["channel_type"] = ChannelType(type_str)
        except ValueError:
            kwargs["channel_type"] = ChannelType.TEXT

    if body.topic:
        kwargs["topic"] = body.topic
    if body.category_id:
        kwargs["category_id"] = int(body.category_id)
    if body.nsfw is not None:
        kwargs["nsfw"] = body.nsfw
    if body.slowmode_seconds is not None:
        kwargs["slowmode_seconds"] = body.slowmode_seconds
    if body.read_receipts_enabled is not None:
        kwargs["read_receipts_enabled"] = body.read_receipts_enabled

    try:
        channel = servers_mod.create_channel(**kwargs)
        invalidate_server_channels(sid)

        response = _channel_to_response(channel)

        import asyncio

        async def dispatch_channel_create():
            try:
                from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                from src.core.events.models import Event
                from src.core.events.types import EventType

                if ws_is_setup():
                    dispatcher = get_dispatcher()
                    user_ids = servers_mod.get_member_user_ids(sid)

                    if user_ids:
                        event = Event(
                            event_type=EventType.CHANNEL_CREATE,
                            data=response.model_dump(),
                            server_id=sid,
                        )
                        await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to broadcast CHANNEL_CREATE: {e}")

        asyncio.create_task(dispatch_channel_create())

        return response
    except HTTPException:
        raise
    except TypeError as e:
        if "unexpected keyword argument" in str(e):
            try:
                channel = servers_mod.create_channel(
                    user_id=current_user.user_id, server_id=sid, name=body.name
                )
                invalidate_server_channels(sid)
                return _channel_to_response(channel)
            except Exception as inner_e:
                logger.error(
                    f"Failed to create channel in server {server_id} (fallback): {inner_e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": str(inner_e)}},
                )

        logger.error(
            f"Failed to create channel in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to create channel in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{server_id}/invites",
    response_model=List[InviteResponse],
    summary="List server invites",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_invites(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[InviteResponse]:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        invites = servers_mod.get_invites(current_user.user_id, sid)
        return [
            InviteResponse(
                code=inv.code,
                server_id=SnowflakeID(inv.server_id),
                channel_id=SnowflakeID(inv.channel_id)
                if hasattr(inv, "channel_id")
                else None,
                inviter_id=SnowflakeID(inv.inviter_id)
                if hasattr(inv, "inviter_id")
                else None,
                uses=getattr(inv, "uses", 0),
                max_uses=getattr(inv, "max_uses", 0),
                max_age=getattr(inv, "max_age", 86400),
                temporary=getattr(inv, "temporary", False),
                created_at=getattr(inv, "created_at", 0),
                expires_at=getattr(inv, "expires_at", None),
            )
            for inv in (invites or [])
        ]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to fetch invites for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
