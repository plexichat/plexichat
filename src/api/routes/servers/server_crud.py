from typing import List
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    ServerCreateRequest,
    ServerUpdateRequest,
    ServerResponse,
    TransferOwnershipRequest,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.database import (
    invalidate_user_servers,
    invalidate_server_channels,
    invalidate_server,
    cached,
)

import utils.logger as logger
from .helpers import _server_to_response

router = APIRouter()


@router.get(
    "/",
    response_model=List[ServerResponse],
    summary="List joined servers",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="user_servers_api")
def get_servers(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[ServerResponse]:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        servers = servers_mod.get_servers(current_user.user_id)
        if servers is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch servers"}},
            )
        return [_server_to_response(s) for s in servers]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to fetch servers for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/",
    response_model=ServerResponse,
    summary="Create server",
    responses={
        400: {"model": ErrorResponse, "description": "Limit reached or invalid data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_server(
    body: ServerCreateRequest, current_user: TokenInfo = Depends(get_current_user)
) -> ServerResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        # L4: the client sends icon_url on creation (api/servers.js) but it
        # was silently dropped here; create_server supports it. Pass it
        # through so a newly created server keeps its chosen icon.
        server = servers_mod.create_server(
            owner_id=current_user.user_id,
            name=body.name,
            description=body.description,
            icon_url=body.icon_url,
        )
        invalidate_user_servers(current_user.user_id)
        return _server_to_response(server)
    except Exception as e:
        exc_name = type(e).__name__
        if "Limit" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid request"}},
            )

        logger.error(
            f"Failed to create server for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{server_id}",
    response_model=ServerResponse,
    summary="Get server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60)
async def get_server(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> ServerResponse:
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

        server = servers_mod.get_server(sid, current_user.user_id)
        if not server:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        return _server_to_response(server)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(f"Failed to get server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{server_id}",
    response_model=ServerResponse,
    summary="Update server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_server(
    server_id: str,
    body: ServerUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ServerResponse:
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

        update_data = body.model_dump(exclude_unset=True)
        server = servers_mod.update_server(current_user.user_id, sid, **update_data)
        if "default_channel_id" in update_data:
            invalidate_server_channels(sid)
        invalidate_server(sid)
        return _server_to_response(server)
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
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(f"Failed to update server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/{server_id}/transfer",
    response_model=ServerResponse,
    summary="Transfer server ownership",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server or user ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {
            "model": ErrorResponse,
            "description": "Only the owner can transfer ownership",
        },
        404: {"model": ErrorResponse, "description": "Server or member not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def transfer_ownership(
    server_id: str,
    body: TransferOwnershipRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ServerResponse:
    """Transfer ownership to another member.

    H1: the core ``transfer_ownership`` primitive (owner-only, new owner
    must already be a member, caches invalidated, audit-logged) previously
    had no HTTP route, so ownership could never change hands through the
    API. Only the current owner may call this endpoint.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            new_owner = int(body.new_owner_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server or user ID"}},
            )

        server = servers_mod.transfer_ownership(current_user.user_id, sid, new_owner)
        invalidate_server(sid)
        invalidate_user_servers(current_user.user_id)
        invalidate_user_servers(new_owner)
        return _server_to_response(server)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {"code": 404, "message": "Server or member not found"}
                },
            )
        elif "Permission" in exc_name or "AccessDenied" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Only the owner can transfer ownership",
                    }
                },
            )

        logger.error(
            f"Failed to transfer server {server_id} ownership: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{server_id}",
    response_model=SuccessResponse,
    summary="Delete server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_server(
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

        servers_mod.delete_server(current_user.user_id, sid)
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
        elif "Permission" in exc_name or "Owner" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(f"Failed to delete server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
