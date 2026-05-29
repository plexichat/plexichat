from typing import List
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    RoleResponse,
    RoleCreateRequest,
    RoleUpdateRequest,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.database import cached

import utils.logger as logger

router = APIRouter()


@router.get(
    "/{server_id}/roles",
    response_model=List[RoleResponse],
    summary="List server roles",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=300)
async def get_server_roles(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[RoleResponse]:
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

    try:
        roles = servers_mod.get_roles(current_user.user_id, sid)
        return [
            RoleResponse(
                id=SnowflakeID(r.id),
                server_id=SnowflakeID(sid),
                name=r.name,
                color=getattr(r, "color", None),
                position=getattr(r, "position", 0),
                permissions=getattr(r, "permissions", {}),
                hoist=getattr(r, "hoist", False),
                mentionable=getattr(r, "mentionable", False),
                is_default=getattr(r, "is_default", False),
            )
            for r in (roles or [])
        ]
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
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/{server_id}/roles",
    response_model=RoleResponse,
    summary="Create role",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_role(
    server_id: str,
    body: RoleCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> RoleResponse:
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

        servers_mod.require_permission(current_user.user_id, sid, "roles.manage")
        role = servers_mod.create_role(
            user_id=current_user.user_id,
            server_id=sid,
            name=body.name,
            color=body.color,
            permissions=body.permissions,
            hoist=body.hoist,
            mentionable=body.mentionable,
        )
        return RoleResponse(
            id=SnowflakeID(role.id),
            server_id=SnowflakeID(sid),
            name=role.name,
            color=getattr(role, "color", None),
            position=getattr(role, "position", 0),
            permissions=getattr(role, "permissions", {}),
            hoist=getattr(role, "hoist", False),
            mentionable=getattr(role, "mentionable", False),
        )
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

        logger.error(f"Failed to create role in server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/{server_id}/roles/{role_id}",
    response_model=RoleResponse,
    summary="Update role",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_role(
    server_id: str,
    role_id: str,
    body: RoleUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> RoleResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        servers_mod.require_permission(current_user.user_id, sid, "roles.manage")
        update_data = body.model_dump(exclude_unset=True)
        role = servers_mod.update_role(current_user.user_id, rid, **update_data)
        return RoleResponse(
            id=SnowflakeID(role.id),
            server_id=SnowflakeID(sid),
            name=role.name,
            color=getattr(role, "color", None),
            position=getattr(role, "position", 0),
            permissions=getattr(role, "permissions", {}),
            hoist=getattr(role, "hoist", False),
            mentionable=getattr(role, "mentionable", False),
            is_default=getattr(role, "is_default", False),
        )
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Role not found"}},
            )
        elif "Permission" in exc_name or "Default" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to update role {role_id} in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{server_id}/roles/{role_id}",
    response_model=SuccessResponse,
    summary="Delete role",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_role(
    server_id: str, role_id: str, current_user: TokenInfo = Depends(get_current_user)
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
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        servers_mod.require_permission(current_user.user_id, sid, "roles.manage")
        servers_mod.delete_role(current_user.user_id, rid)
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Role not found"}},
            )
        elif "Permission" in exc_name or "Default" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to delete role {role_id} in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
