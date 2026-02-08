"""
Notifications API routes.

Handles user notifications.
"""

import utils.logger as logger
from fastapi import APIRouter, Depends, HTTPException

from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.notifications import NotificationsResponse
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["Notifications"])


@router.get(
    "/users/@me/notifications",
    response_model=NotificationsResponse,
    summary="Get my notifications",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_notifications(
    limit: int = 20, current_user: TokenInfo = Depends(get_current_user)
) -> NotificationsResponse:
    """
    Get user notifications.

    Returns an empty list for now (placeholder).
    """
    try:
        logger.debug(
            f"User {current_user.user_id} requested notifications (limit={limit})"
        )
        # Return empty notifications list for now
        return NotificationsResponse(notifications=[], unread_count=0)
    except Exception as e:
        logger.error(
            f"Failed to get notifications for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.put(
    "/users/@me/notifications/read-all",
    response_model=SuccessResponse,
    summary="Mark all notifications as read",
)
async def mark_all_read(
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Mark all user notifications as read (placeholder)."""
    return SuccessResponse(success=True)


@router.put(
    "/users/@me/notifications/{notification_id}/read",
    response_model=SuccessResponse,
    summary="Mark notification as read",
)
async def mark_read(
    notification_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Mark a specific notification as read (placeholder)."""
    return SuccessResponse(success=True)
