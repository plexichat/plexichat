import asyncio
from typing import Dict

from fastapi import HTTPException, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
import src.core.events as events_mod
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.channels import (
    ChannelInviteCreateRequest,
    ChannelInviteResponse,
    InviteInfoResponse,
    InviteJoinResponse,
)
from src.api.schemas.common import ErrorResponse, SnowflakeID

import utils.logger as logger

from .base import ChannelBase


class ChannelInvitesMixin(ChannelBase):
    def _register_routes(self) -> None:
        self.router.add_api_route(
            "/{channel_id}/invites",
            self._create_channel_invite,
            methods=["POST"],
            response_model=ChannelInviteResponse,
            summary="Create channel invite",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid channel ID or data",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Channel not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/invites/{invite_code}",
            self._get_invite_info,
            methods=["GET"],
            response_model=InviteInfoResponse,
            summary="Get invite info",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                404: {
                    "model": ErrorResponse,
                    "description": "Invite not found or expired",
                },
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/invites/{invite_code}",
            self._join_server_via_invite,
            methods=["POST"],
            response_model=InviteJoinResponse,
            summary="Join server via invite",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Banned from server"},
                404: {
                    "model": ErrorResponse,
                    "description": "Invite not found or expired",
                },
                409: {"model": ErrorResponse, "description": "Already a member"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )
        self.router.add_api_route(
            "/invites/{invite_code}",
            self._delete_invite,
            methods=["DELETE"],
            summary="Delete invite",
            responses={
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {"model": ErrorResponse, "description": "Access denied"},
                404: {"model": ErrorResponse, "description": "Invite not found"},
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )

    async def _create_channel_invite(
        self,
        channel_id: str,
        body: ChannelInviteCreateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> ChannelInviteResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
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
                # SELFTEST FIX: pre-check channel visibility so we
                # return 404 (channel gone) / 403 (channel exists
                # but caller blocked) distinctly instead of letting
                # ``create_invite`` raise and conflate both in a
                # single status code.
                if not servers_mod.channel_exists(cid):
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Channel not found",
                            }
                        },
                    )
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if not channel:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Channel access denied",
                            }
                        },
                    )
                try:
                    invite = servers_mod.create_invite(
                        user_id=current_user.user_id,
                        channel_id=cid,
                        max_age=body.max_age,
                        max_uses=body.max_uses,
                        temporary=body.temporary,
                    )
                except Exception as exc:  # noqa: BLE001
                    exc_name = type(exc).__name__
                    if "Permission" in exc_name or "Access" in exc_name:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": {
                                    "code": 403,
                                    "message": str(exc),
                                }
                            },
                        )
                    raise
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
            except HTTPException:
                # Re-raise HTTPException FIRST so the dual-probe's
                # 404/403 status codes survive the outer ``except
                # Exception`` block that follows. The outer handler
                # categorises non-HTTPException errors by class-name
                # substring (NotFound / Permission / Access / etc.),
                # but HTTPException's class name doesn't match any of
                # those substrings -- so without this re-raise the
                # dual-probe's status code gets degraded to a generic
                # 500. SELFTEST FIX.
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
                        f"User {current_user.user_id} denied permission to create invite for channel {cid}"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": str(e)}},
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

    async def _get_invite_info(
        self, invite_code: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> InviteInfoResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                invite = servers_mod.get_invite(invite_code)
                if not invite:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Invite not found or expired",
                            }
                        },
                    )

                server_name = getattr(invite, "server_name", None)
                if not server_name and hasattr(invite, "server_id"):
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
                if any(
                    keyword in exc_name.lower()
                    for keyword in ["notfound", "expired", "invalid", "missing"]
                ):
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Invite not found or expired",
                            }
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
                f"Unexpected error in get_invite_info for {invite_code}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def _join_server_via_invite(
        self, invite_code: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> InviteJoinResponse:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        try:
            try:
                result = servers_mod.use_invite(current_user.user_id, invite_code)

                sid = getattr(result, "server_id", None) or (
                    result if isinstance(result, (int, str)) else None
                )

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

                        async def _fetch_sync(fn, *args):
                            return await run_in_threadpool(fn, *args)

                        results = await asyncio.gather(
                            _fetch_sync(
                                servers_mod.get_server, sid, current_user.user_id
                            ),
                            _fetch_sync(
                                servers_mod.get_channels, current_user.user_id, sid
                            ),
                            _fetch_sync(
                                servers_mod.get_roles, current_user.user_id, sid
                            ),
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
                            await dispatcher.dispatch_event(
                                event, [current_user.user_id]
                            )

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
                            "error": {
                                "code": 404,
                                "message": "Invite not found or expired",
                            }
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
                    f"Failed to join server via invite {invite_code}: {e}",
                    exc_info=True,
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

    async def _delete_invite(
        self, invite_code: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> Dict[str, bool]:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
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
                        status_code=403,
                        detail={"error": {"code": 403, "message": str(e)}},
                    )

                logger.error(
                    f"Failed to delete invite {invite_code}: {e}", exc_info=True
                )
                raise HTTPException(
                    status_code=500, detail={"error": {"code": 500, "message": str(e)}}
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in delete_invite for {invite_code}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
