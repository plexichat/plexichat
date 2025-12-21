"""
Notifications API routes.

Handles user notifications (placeholder for now).
"""

from typing import Dict, Any
from fastapi import APIRouter, Depends

from src.api.middleware.authentication import get_current_user, TokenInfo


router = APIRouter()


@router.get("/users/@me/notifications")
async def get_notifications(
    limit: int = 20,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get user notifications.
    
    Returns an empty list for now (placeholder).
    """
    # Return empty notifications list for now
    return {
        "notifications": [],
        "unread_count": 0
    }
