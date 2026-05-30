"""
Channel routes - Channel management endpoints.
"""

import asyncio
from typing import Dict, List
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Body
from starlette.concurrency import run_in_threadpool

import src.api as api
import src.core.events as events_mod
from src.core.database import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    ChannelResponse,
    ChannelUpdateRequest,
    WebhookResponse,
)
from src.api.schemas.channels import (
    ChannelInviteCreateRequest,
    ChannelInviteResponse,
    InviteInfoResponse,
    InviteJoinResponse,
    AttachmentUploadResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse

import utils.logger as logger
from .helpers import _channel_to_response

router = APIRouter(tags=["Channels"])


@router.get(
    "/{channel_id}",
    response_model=ChannelResponse,
    summary="Get channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="channel_api")
async def get_channel(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> ChannelResponse:
    """
    Get channel by ID.

    Returns channel information if the user has access.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:

            def _get_channel_sync():
                import src.api as api_module

                db = api_module.get_db()
                try:
                    ch = servers_mod.get_channel(cid, current_user.user_id)
                    if not ch:
                        # Fallback to messaging module for DMs
                        messaging_mod = api_module.get_messaging()
                        if messaging_mod:
                            ch = messaging_mod.get_conversation(
                                cid, current_user.user_id
                            )
                    return ch
                finally:
                    if db:
                        db.close()

            channel = await run_in_threadpool(_get_channel_sync)

            if not channel:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            return _channel_to_response(channel, current_user.user_id)
        except HTTPException:
            raise
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            elif "Access" in exc_name or "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied access to channel {cid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )

            logger.error(
                f"Failed to get channel {cid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_channel for {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/{channel_id}",
    response_model=ChannelResponse,
    summary="Update channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_channel(
    channel_id: str,
    body: ChannelUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ChannelResponse:
    """
    Update channel settings.

    Updates channel information. Requires manage channels permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format for update: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            update_data = body.model_dump(exclude_unset=True)

            def _update_channel_sync():
                import src.api as api_module

                db = api_module.get_db()
                try:
                    return servers_mod.update_channel(
                        current_user.user_id, cid, **update_data
                    )
                finally:
                    if db:
                        db.close()

            channel = await run_in_threadpool(_update_channel_sync)

            response = _channel_to_response(channel, current_user.user_id)
            sid = channel.server_id

            # Broadcast CHANNEL_UPDATE event via WebSocket (fire and forget)
            import asyncio

            async def dispatch_channel_update():
                try:
                    from src.api.websocket import (
                        get_dispatcher,
                        is_setup as ws_is_setup,
                    )
                    from src.core.events.models import Event
                    from src.core.events.types import EventType

                    if ws_is_setup():
                        dispatcher = get_dispatcher()

                        def _get_member_ids_sync():
                            import src.api as api_module

                            db = api_module.get_db()
                            try:
                                return servers_mod.get_member_user_ids(sid)
                            finally:
                                if db:
                                    db.close()

                        user_ids = await run_in_threadpool(_get_member_ids_sync)

                        if user_ids:
                            event = Event(
                                event_type=EventType.CHANNEL_UPDATE,
                                data=response.model_dump(),
                                server_id=sid,
                                channel_id=cid,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                except Exception as e:
                    logger.debug(
                        f"Failed to broadcast CHANNEL_UPDATE for channel {cid}: {e}"
                    )

            asyncio.create_task(dispatch_channel_update())

            return response
        except HTTPException:
            raise
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to update channel {cid}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )

            logger.error(
                f"Failed to update channel {cid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in update_channel for {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{channel_id}",
    summary="Delete channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_channel(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
    """
    Delete a channel.

    Permanently deletes the channel. Requires manage channels permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format for deletion: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            # Get channel details BEFORE deleting for broadcast
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if not channel:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )

            sid = channel.server_id

            servers_mod.delete_channel(current_user.user_id, cid)

            # Broadcast CHANNEL_DELETE event via WebSocket (fire and forget)
            import asyncio

            async def dispatch_channel_delete():
                try:
                    from src.api.websocket import (
                        get_dispatcher,
                        is_setup as ws_is_setup,
                    )
                    from src.core.events.models import Event
                    from src.core.events.types import EventType

                    if ws_is_setup():
                        dispatcher = get_dispatcher()
                        user_ids = servers_mod.get_member_user_ids(sid)

                        if user_ids:
                            event = Event(
                                event_type=EventType.CHANNEL_DELETE,
                                data={"id": str(cid), "server_id": str(sid)},
                                server_id=sid,
                                channel_id=cid,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                except Exception as e:
                    logger.debug(
                        f"Failed to broadcast CHANNEL_DELETE for channel {cid}: {e}"
                    )

            asyncio.create_task(dispatch_channel_delete())

            return {"success": True}
        except HTTPException:
            raise
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to delete channel {cid}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )

            logger.error(
                f"Failed to delete channel {cid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_channel for {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{channel_id}/webhooks",
    response_model=List[WebhookResponse],
    summary="Get channel webhooks",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_channel_webhooks(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[WebhookResponse]:
    """
    Get all webhooks for a channel.

    Requires manage webhooks permission.
    """
    webhooks_mod = api.get_webhooks()
    if not webhooks_mod:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Webhooks module not available"}},
        )

    try:
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format for webhooks: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            webhooks = await run_in_threadpool(
                webhooks_mod.get_channel_webhooks, current_user.user_id, cid
            )
            return [
                WebhookResponse(
                    id=SnowflakeID(w.id),
                    channel_id=SnowflakeID(w.channel_id),
                    server_id=SnowflakeID(w.server_id),
                    creator_id=SnowflakeID(getattr(w, "creator_id", 0))
                    if getattr(w, "creator_id", 0)
                    else None,
                    name=w.name,
                    avatar_url=w.avatar_url,
                    created_at=w.created_at,
                )
                for w in (webhooks or [])
            ]
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            elif "Permission" in exc_name or "Access" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied access to webhooks for channel {cid}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )

            logger.error(
                f"Failed to get webhooks for channel {cid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_channel_webhooks for {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/{channel_id}/invites",
    response_model=ChannelInviteResponse,
    summary="Create channel invite",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_channel_invite(
    channel_id: str,
    body: ChannelInviteCreateRequest = Body(default=ChannelInviteCreateRequest()),
    current_user: TokenInfo = Depends(get_current_user),
) -> ChannelInviteResponse:
    """
    Create an invite for a channel.

    Requires create instant invite permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format for invite: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            invite = servers_mod.create_invite(
                user_id=current_user.user_id,
                channel_id=cid,
                max_age=body.max_age,
                max_uses=body.max_uses,
                temporary=body.temporary,
            )
            return ChannelInviteResponse(
                code=invite.code,
                channel_id=SnowflakeID(cid),
                server_id=SnowflakeID(invite.server_id)
                if hasattr(invite, "server_id")
                else None,
                max_age=body.max_age,
                max_uses=body.max_uses,
                temporary=body.temporary,
                uses=0,
                created_at=getattr(invite, "created_at", None),
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to create invite for channel {cid}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )

            logger.error(
                f"Failed to create invite for channel {cid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in create_channel_invite for {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Global Invite Routes ====================


@router.get(
    "/invites/{invite_code}",
    response_model=InviteInfoResponse,
    summary="Get invite info",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Invite not found or expired"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_invite_info(
    invite_code: str, current_user: TokenInfo = Depends(get_current_user)
) -> InviteInfoResponse:
    """
    Get invite information.

    Returns details about an invite without joining.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            invite = servers_mod.get_invite(invite_code)
            if not invite:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Invite not found or expired"}
                    },
                )

            server_name = getattr(invite, "server_name", None)
            if not server_name and hasattr(invite, "server_id"):
                # Try to fetch server name if not in invite object
                try:
                    server = servers_mod.get_server(
                        invite.server_id, current_user.user_id
                    )
                    if server:
                        server_name = server.name
                except Exception:
                    pass

            return InviteInfoResponse(
                code=invite.code,
                server_id=SnowflakeID(invite.server_id)
                if hasattr(invite, "server_id")
                else None,
                server_name=server_name,
                channel_id=SnowflakeID(invite.channel_id)
                if hasattr(invite, "channel_id")
                else None,
                inviter_id=SnowflakeID(invite.inviter_id)
                if hasattr(invite, "inviter_id")
                else None,
                uses=getattr(invite, "uses", 0),
                max_uses=getattr(invite, "max_uses", 0),
                expires_at=getattr(invite, "expires_at", None),
            )
        except HTTPException:
            raise
        except Exception as e:
            exc_name = type(e).__name__
            # Catch all not-found related exceptions and return 404
            if any(
                keyword in exc_name.lower()
                for keyword in ["notfound", "expired", "invalid", "missing"]
            ):
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Invite not found or expired"}
                    },
                )

            logger.error(
                f"Failed to get info for invite {invite_code}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_invite_info for {invite_code}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/invites/{invite_code}",
    response_model=InviteJoinResponse,
    summary="Join server via invite",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Banned from server"},
        404: {"model": ErrorResponse, "description": "Invite not found or expired"},
        409: {"model": ErrorResponse, "description": "Already a member"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def join_server_via_invite(
    invite_code: str, current_user: TokenInfo = Depends(get_current_user)
) -> InviteJoinResponse:
    """
    Join a server via invite code.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            result = servers_mod.use_invite(current_user.user_id, invite_code)

            # The result might be a Server object or just the ID
            sid = getattr(result, "server_id", None) or (
                result if isinstance(result, (int, str)) else None
            )

            # Dispatch WebSocket events
            async def dispatch_join_events():
                try:
                    from src.api.websocket import (
                        get_dispatcher,
                        is_setup as ws_is_setup,
                    )

                    if not ws_is_setup():
                        return

                    dispatcher = get_dispatcher()
                    auth = api.get_auth()

                    # 1. Dispatch GUILD_CREATE to the joining user
                    # Fetch server, channels, and roles in parallel via threadpool.
                    # Use return_exceptions=True so a roles.fetch failure (which
                    # requires roles.manage permission that a new member lacks) doesn't
                    # discard the successfully-fetched server and channels.
                    async def _fetch_sync(fn, *args):
                        return await run_in_threadpool(fn, *args)

                    results = await asyncio.gather(
                        _fetch_sync(servers_mod.get_server, sid, current_user.user_id),
                        _fetch_sync(
                            servers_mod.get_channels, current_user.user_id, sid
                        ),
                        _fetch_sync(servers_mod.get_roles, current_user.user_id, sid),
                        return_exceptions=True,
                    )
                    server, channels, roles = results
                    if isinstance(roles, BaseException):
                        roles = []
                    if isinstance(channels, BaseException):
                        channels = []
                    if server and not isinstance(server, BaseException):
                        event = events_mod.create_guild_create(
                            server_id=int(sid or 0),
                            name=server.name,
                            owner_id=server.owner_id,
                            member_count=getattr(server, "member_count", 0),
                            channels=[
                                {
                                    "id": str(c.id),
                                    "name": c.name,
                                    "type": getattr(
                                        c.channel_type, "value", c.channel_type
                                    )
                                    if hasattr(c.channel_type, "value")
                                    else c.channel_type,
                                    "position": getattr(c, "position", 0),
                                }
                                for c in channels
                            ],
                            roles=[
                                {
                                    "id": str(r.id),
                                    "name": r.name,
                                    "color": r.color,
                                    "hoist": r.hoist,
                                    "position": r.position,
                                }
                                for r in roles
                            ],
                        )
                        await dispatcher.dispatch_event(event, [current_user.user_id])

                    # 2. Dispatch GUILD_MEMBER_ADD to other members
                    user_data = None
                    if auth:
                        user = await run_in_threadpool(
                            auth.get_user, current_user.user_id
                        )
                        if user:
                            user_data = {
                                "id": str(user.id),
                                "username": user.username,
                                "avatar_url": user.avatar_url,
                            }

                    member_event = events_mod.create_guild_member_add(
                        server_id=int(sid or 0),
                        user_id=current_user.user_id,
                        user=user_data,
                    )

                    # Get member IDs to notify
                    member_ids = servers_mod.get_member_user_ids(
                        sid, exclude_user_id=current_user.user_id
                    )
                    if member_ids:
                        await dispatcher.dispatch_event(member_event, member_ids)

                except Exception as de:
                    logger.warning(
                        f"Failed to dispatch join events for server {sid}: {de}"
                    )

            asyncio.create_task(dispatch_join_events())

            return InviteJoinResponse(
                success=True,
                server_id=SnowflakeID(sid) if sid else None,
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name or "Expired" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Invite not found or expired"}
                    },
                )
            elif "Already" in exc_name or "Member" in exc_name:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": 409,
                            "message": "Already a member of this server",
                        }
                    },
                )
            elif "Banned" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} attempted to join server via {invite_code} but is banned"
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "You are banned from this server",
                        }
                    },
                )

            logger.error(
                f"Failed to join server via invite {invite_code}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in join_server_via_invite for {invite_code}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/invites/{invite_code}",
    summary="Delete invite",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Invite not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_invite(
    invite_code: str, current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
    """
    Delete an invite.

    Requires manage server permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            servers_mod.delete_invite(current_user.user_id, invite_code)
            return {"success": True}
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Invite not found"}},
                )
            elif "Permission" in exc_name or "Access" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to delete invite {invite_code}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )

            logger.error(f"Failed to delete invite {invite_code}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_invite for {invite_code}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/{channel_id}/attachments",
    response_model=AttachmentUploadResponse,
    summary="Upload attachment",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid channel ID or file too large/unsupported",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_attachment(
    channel_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> AttachmentUploadResponse:
    """
    Upload a file attachment to a channel.

    Returns the URL of the uploaded file.
    File size limit is based on user's tier (alpha users get 25MB, premium 100MB, etc.)
    """
    servers_mod = api.get_servers()
    messaging = api.get_messaging()
    media = api.get_media()

    if not media:
        logger.error("Media module not available for attachment upload")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Media module not available"}},
        )

    try:
        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format for attachment: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # Verify user has access to channel (try server channel first, then DM)
        has_access = False

        if servers_mod:
            try:
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if channel:
                    has_access = True
            except Exception:
                pass

        # If not a server channel, check if it's a DM conversation
        if not has_access and messaging:
            try:
                conv = messaging.get_conversation(cid, current_user.user_id)
                if conv:
                    has_access = True
            except Exception:
                pass

        if not has_access:
            logger.warning(
                f"User {current_user.user_id} denied access to channel {cid} for attachment upload"
            )
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )

        # Use the media module for upload (handles size limits, security, and storage)
        try:

            def _upload_with_cleanup():
                import src.api as api_module

                db = api_module.get_db()
                try:
                    stream = file.file
                    stream.seek(0)
                    return media.upload_stream(
                        user_id=current_user.user_id,
                        stream=stream,
                        filename=file.filename or "attachment",
                        content_type=file.content_type or "application/octet-stream",
                        size=file.size or 0,
                    )
                finally:
                    if db:
                        db.close()

            result = await run_in_threadpool(_upload_with_cleanup)

            # Convert thumbnail keys from int to str for Pydantic schema
            thumbnails_str = (
                {str(k): v for k, v in result.thumbnails.items()}
                if result.thumbnails
                else None
            )
            return AttachmentUploadResponse(
                id=str(result.file_id),
                filename=result.filename,
                size=result.size,
                content_type=result.content_type,
                url=result.url,
                hash=result.checksum,
                thumbnails=thumbnails_str,
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "Size" in exc_name:
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )
            elif "Type" in exc_name:
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )
            elif "Blocked" in exc_name or "Malware" in exc_name:
                logger.warning(
                    f"File upload blocked for user {current_user.user_id}: {e}"
                )
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )

            logger.error(
                f"Attachment upload failed for channel {cid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Upload failed"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in upload_attachment for {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
