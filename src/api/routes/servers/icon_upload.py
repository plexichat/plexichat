from fastapi import APIRouter, HTTPException, Depends, File, UploadFile

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.servers import ServerResponse
from src.api.schemas.common import ErrorResponse

import utils.logger as logger
from src.api.request_guards import read_upload_capped
from src.core.security.hardening import parse_id
from .helpers import _server_to_response, _get_icon_size_limit

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
        413: {"model": ErrorResponse, "description": "Icon exceeds the size limit"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_server_icon(
    server_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> ServerResponse:
    require_user_token(current_user)

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
            sid = parse_id(server_id, "server ID")
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

        # Refuse non-image parts before reading any bytes. The avatars manager
        # also validates MIME/magic bytes after the read, but rejecting early
        # avoids spending memory and decoding work on an unusable payload.
        if not file.content_type or not file.content_type.lower().startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid image file"}},
            )

        # Cap the read at the configured icon limit *before* materialising the
        # part. ``UploadFile.read()`` with no argument allocates the whole body
        # in memory, so a large upload could exhaust worker memory long before
        # the avatars module's post-read size check ever ran. The sibling
        # /avatars/servers/{id} route uses the same guard.
        content = await read_upload_capped(file, _get_icon_size_limit())
        result = avatars.upload_server_icon(
            server_id=sid,
            image_data=content,
            content_type=file.content_type,
        )

        # H2: parity with /avatars/servers/{id} — push the new icon to
        # connected clients so open sessions are not stale.
        try:
            from src.core.events.gateway_emit import emit_guild_update

            emit_guild_update(sid, icon=result["url"])
        except Exception as ge:
            logger.debug(f"emit_guild_update (icon upload) failed: {ge}")

        server = servers_mod.update_server(
            current_user.user_id, sid, icon_url=result["url"]
        )
        return _server_to_response(server)
    except HTTPException:
        raise
    except (ValueError, TypeError):
        # M1: invalid image bytes are a CLIENT error - same static message
        # as the sibling /avatars endpoints; never leak str(e).
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid image file"}},
        )
    except Exception as e:
        exc_name = type(e).__name__
        # M1: invalid image data (size/type/blocked/malware) is a CLIENT
        # error - the response must say so, not "Internal server error".
        if "Size" in exc_name or "Type" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid image file"}},
            )
        elif "Blocked" in exc_name or "Malware" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid image file"}},
            )

        logger.error(
            f"Server icon upload failed for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Upload failed"}}
        )
