"""Storage quota endpoints for the current user."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.routes.users.helpers import require_user_token
from src.core.security.hardening import HardeningViolation
from src.core.storage_quota import get_manager

import utils.logger as logger

router = APIRouter(prefix="/storage", tags=["Storage"])


@router.get("/quota", summary="Get my storage usage and limit")
async def get_my_storage_quota(
    current_user: TokenInfo = Depends(get_current_user),
) -> dict:
    require_user_token(current_user)
    try:
        return get_manager().get_status(current_user.user_id)
    except Exception as exc:
        logger.error(
            "Could not read storage quota for user %s: %s",
            current_user.user_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": 500,
                    "message": "Storage quota is temporarily unavailable",
                }
            },
        ) from exc


@router.get("/usage", summary="Alias for my storage usage and limit")
async def get_my_storage_usage(
    current_user: TokenInfo = Depends(get_current_user),
) -> dict:
    require_user_token(current_user)
    return await get_my_storage_quota(current_user)


@router.get("/files", summary="List my stored files")
async def list_my_storage_files(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    path: Optional[str] = Query(None, max_length=512),
    current_user: TokenInfo = Depends(get_current_user),
) -> dict:
    """List a bounded page of the caller's files, optionally under ``path``."""
    require_user_token(current_user)
    try:
        return get_manager().list_user_files(
            current_user.user_id, limit=limit, offset=offset, path_prefix=path
        )
    except HardeningViolation as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(exc)}},
        ) from exc
    except Exception as exc:
        logger.error(
            "Could not list storage files for user %s: %s",
            current_user.user_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Storage listing is unavailable"}
            },
        ) from exc
