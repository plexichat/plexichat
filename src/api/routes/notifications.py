"""
Notifications API routes.

Handles user notifications.
"""

import asyncio

import utils.logger as logger
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.notifications import NotificationsResponse, NotificationInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["Notifications"])


def _notif_to_response(notif) -> NotificationInfo:
    """Convert core notification model to API response schema."""
    # Determine type and title
    notif_type = "mention"  # Default
    title = "New Notification"

    m_type = getattr(notif.mention_type, "value", str(notif.mention_type))

    if m_type == "user":
        title = "You were mentioned"
    elif m_type == "role":
        title = "Your role was mentioned"
    elif m_type == "everyone" or m_type == "here":
        title = f"@{m_type} mention"

    # Construct link
    link = None
    if notif.server_id:
        link = f"/channels/{notif.server_id}/{notif.channel_id}/{notif.message_id}"
    elif notif.conversation_id:
        link = f"/channels/@me/{notif.conversation_id}/{notif.message_id}"

    return NotificationInfo(
        id=str(notif.id),
        type=notif_type,
        title=title,
        content=notif.content_preview or "",
        content_preview=notif.content_preview or "",
        read=bool(notif.read),
        created_at=notif.created_at,
        link=link,
        sender_id=str(notif.author_id),
    )


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
    limit: int = 20,
    unread_only: bool = False,
    current_user: TokenInfo = Depends(get_current_user),
) -> NotificationsResponse:
    """
    Get user notifications.
    """
    notif_mod = api.get_notifications()
    if not notif_mod:
        raise HTTPException(status_code=500, detail="Notification module not available")

    try:
        notifications, unread_count = await asyncio.gather(
            run_in_threadpool(
                notif_mod.get_notifications,
                current_user.user_id,
                limit=limit,
                unread_only=unread_only,
            ),
            run_in_threadpool(notif_mod.get_mention_count, current_user.user_id),
        )

        return NotificationsResponse(
            notifications=[_notif_to_response(n) for n in notifications],
            unread_count=unread_count,
        )
    except Exception as e:
        logger.error(
            f"Failed to get notifications for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.put(
    "/users/@me/notifications/read-all",
    response_model=SuccessResponse,
    summary="Mark all notifications as read",
)
async def mark_all_read(
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Mark all user notifications as read."""
    notif_mod = api.get_notifications()
    if not notif_mod:
        raise HTTPException(status_code=500, detail="Notification module not available")

    try:
        notif_mod.mark_all_read(current_user.user_id)
        return SuccessResponse(success=True, message=None)
    except Exception as e:
        logger.error(f"Failed to mark all notifications read: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/users/@me/notifications/{notification_id}/read",
    response_model=SuccessResponse,
    summary="Mark notification as read",
)
async def mark_read(
    notification_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Mark a specific notification as read."""
    notif_mod = api.get_notifications()
    if not notif_mod:
        raise HTTPException(status_code=500, detail="Notification module not available")

    try:
        try:
            nid = int(notification_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid notification ID")

        notif_mod.mark_notification_read(current_user.user_id, nid)
        return SuccessResponse(success=True, message=None)
    except Exception as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Notification not found")
        logger.error(f"Failed to mark notification {notification_id} read: {e}")
        raise HTTPException(status_code=500, detail=str(e))
