from fastapi import APIRouter, HTTPException, Depends, File, UploadFile

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import ServerResponse
from src.api.schemas.common import ErrorResponse

import utils.logger as logger
from .helpers import _server_to_response

router = APIRouter()


@router.post(
    "/{server_id}/icon",
    response_model=ServerResponse,
    summary="Upload server icon",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_server_icon(
    server_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> ServerResponse:
    servers_mod = api.get_servers()
    media = api.get_media()

    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    if not media:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Media module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        server = servers_mod.get_server(sid, current_user.user_id)
        if not server:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )

        servers_mod.require_permission(current_user.user_id, sid, "server.manage")

        avatars = api.get_avatars()
        if not avatars:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Avatars module not available"}
                },
            )

        content = await file.read()
        result = avatars.upload_server_icon(
            server_id=sid,
            image_data=content,
            content_type=file.content_type,
        )

        server = servers_mod.update_server(
            current_user.user_id, sid, icon_url=result["url"]
        )
        return _server_to_response(server)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "Size" in exc_name or "Type" in exc_name:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )
        elif "Blocked" in exc_name or "Malware" in exc_name:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )

        logger.error(
            f"Server icon upload failed for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Upload failed"}}
        )
