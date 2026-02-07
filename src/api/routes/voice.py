"""
Voice routes - Voice channel and WebRTC signaling endpoints.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Depends, status

import utils.logger as logger
from src.core.database.cache import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.voice import (
    ICEServerConfig,
    ICEServersResponse,
    VoiceChannelInfoResponse,
)
from src.api.schemas.common import ErrorResponse

router = APIRouter(prefix="/voice", tags=["Voice"])


@router.get(
    "/ice-servers",
    response_model=ICEServersResponse,
    summary="Get ICE servers",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=3600, prefix="ice_servers")
async def get_ice_servers(
    current_user: TokenInfo = Depends(get_current_user),
) -> ICEServersResponse:
    """
    Get ICE server configuration for WebRTC.

    Returns STUN and TURN servers with credentials for establishing
    peer-to-peer connections through NAT/firewalls.
    """
    try:
        from src.core.voice import signaling

        # Get ICE servers for this user
        info = signaling.get_voice_server_info(current_user.user_id, 0)

        # Convert to client format
        ice_servers: List[ICEServerConfig] = []
        for server in info.ice_servers:
            urls = server.urls
            urls_list: List[str] = [urls] if isinstance(urls, str) else list(urls)
            server_config = ICEServerConfig(
                urls=urls_list, username=server.username, credential=server.credential
            )
            ice_servers.append(server_config)

        return ICEServersResponse(ice_servers=ice_servers)
    except (RuntimeError, Exception) as e:
        logger.warning(
            f"Failed to get ICE servers for user {current_user.user_id}, returning defaults: {e}"
        )
        # Return default STUN servers if signaling not available
        return ICEServersResponse(
            ice_servers=[
                ICEServerConfig(
                    urls=[
                        "stun:stun.l.google.com:19302",
                        "stun:stun1.l.google.com:19302",
                    ]
                )
            ]
        )


@router.get(
    "/channels/{channel_id}/info",
    response_model=VoiceChannelInfoResponse,
    summary="Get voice channel info",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not connected or forbidden"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {
            "model": ErrorResponse,
            "description": "Voice signaling service unavailable",
        },
    },
)
async def get_voice_channel_info(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> VoiceChannelInfoResponse:
    """
    Get voice channel connection info including ICE servers.
    """
    try:
        try:
            cid = int(channel_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid channel ID: {channel_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            from src.core.voice import signaling

            info = signaling.get_voice_server_info(current_user.user_id, cid)

            # Convert ICE servers to client format
            ice_servers: List[ICEServerConfig] = []
            for server in info.ice_servers:
                urls = server.urls
                urls_list: List[str] = [urls] if isinstance(urls, str) else list(urls)
                server_config = ICEServerConfig(
                    urls=urls_list,
                    username=server.username,
                    credential=server.credential,
                )
                ice_servers.append(server_config)

            logger.info(
                f"User {current_user.user_id} requested voice info for channel {cid}"
            )
            return VoiceChannelInfoResponse(
                channel_id=channel_id,
                session_id=info.session_id,
                ice_servers=ice_servers,
                bitrate=info.bitrate,
            )
        except (RuntimeError, Exception) as e:
            exc_name = type(e).__name__
            if isinstance(e, RuntimeError) and "not initialized" in str(e):
                logger.error(
                    f"Voice signaling service unavailable for user {current_user.user_id}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": {
                            "code": 503,
                            "message": "Voice signaling service currently unavailable",
                        }
                    },
                )
            if "NotFound" in exc_name:
                logger.warning(
                    f"Voice channel {cid} not found for user {current_user.user_id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            if "NotConnected" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} not connected to voice channel {cid}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": str(e)}},
                )

            logger.error(
                f"Unexpected error in get_voice_channel_info for channel {cid} (user: {current_user.user_id}): {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected top-level error in get_voice_channel_info for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
