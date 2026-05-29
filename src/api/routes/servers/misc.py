from typing import Dict
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.database import invalidate_user_servers

import utils.logger as logger

router = APIRouter()


@router.post(
    "/{server_id}/leave",
    response_model=SuccessResponse,
    summary="Leave server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Cannot leave as owner"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def leave_server(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
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

        servers_mod.leave_server(current_user.user_id, sid)
        invalidate_user_servers(current_user.user_id)
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Owner" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Owners cannot leave their own server",
                    }
                },
            )

        logger.error(f"Failed to leave server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{server_id}/permissions",
    response_model=Dict[str, bool],
    summary="Get my permissions",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_my_permissions(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
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

        return servers_mod.get_permissions(current_user.user_id, sid)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )

        logger.error(
            f"Failed to get permissions for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
