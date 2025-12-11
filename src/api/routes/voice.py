"""
Voice routes - Voice channel and WebRTC signaling endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends

from src.api.middleware.authentication import get_current_user, TokenInfo

router = APIRouter()


@router.get("/voice/ice-servers")
async def get_ice_servers(
    current_user: TokenInfo = Depends(get_current_user)
):
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
        ice_servers = []
        for server in info.ice_servers:
            server_config = {"urls": server.urls}
            if server.username:
                server_config["username"] = server.username
            if server.credential:
                server_config["credential"] = server.credential
            ice_servers.append(server_config)

        return {"ice_servers": ice_servers}
    except Exception as e:
        # Return default STUN servers if signaling not available
        return {
            "ice_servers": [
                {"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}
            ]
        }


@router.get("/voice/channels/{channel_id}/info")
async def get_voice_channel_info(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get voice channel connection info including ICE servers.
    """
    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        from src.core.voice import signaling

        info = signaling.get_voice_server_info(current_user.user_id, cid)

        # Convert ICE servers to client format
        ice_servers = []
        for server in info.ice_servers:
            server_config = {"urls": server.urls}
            if server.username:
                server_config["username"] = server.username
            if server.credential:
                server_config["credential"] = server.credential
            ice_servers.append(server_config)

        return {
            "channel_id": channel_id,
            "session_id": info.session_id,
            "ice_servers": ice_servers,
            "bitrate": info.bitrate,
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})
