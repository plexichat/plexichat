"""Storage quota endpoints for the current user."""

from fastapi import APIRouter, Depends, HTTPException

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.core.storage_quota import get_manager

import utils.logger as logger

router = APIRouter(prefix="/storage", tags=["Storage"])


@router.get("/quota", summary="Get my storage usage and limit")
async def get_my_storage_quota(
    current_user: TokenInfo = Depends(get_current_user),
) -> dict:
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
    return await get_my_storage_quota(current_user)
