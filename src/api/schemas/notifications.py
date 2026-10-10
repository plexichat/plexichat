"""
Notifications API schemas.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class NotificationInfo(BaseModel):
    """Notification information."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Notification ID")
    type: str = Field(..., description="Notification type")
    title: str = Field(..., description="Notification title")
    content: str = Field(..., description="Notification content")
    content_preview: Optional[str] = Field(None, description="Short preview of content")
    read: bool = Field(False, description="Whether notification has been read")
    created_at: int = Field(..., description="Creation timestamp (Unix)")
    link: Optional[str] = Field(
        None, description="Optional link associated with notification"
    )
    sender_id: Optional[str] = Field(
        None, description="ID of user who triggered notification"
    )


class NotificationsResponse(BaseModel):
    """Response for user notifications."""

    model_config = ConfigDict(from_attributes=True)

    notifications: List[NotificationInfo] = Field(
        ..., description="List of notifications"
    )
    unread_count: int = Field(..., description="Number of unread notifications")
    total_unread: Optional[int] = Field(
        None,
        description=(
            "Exact count of ALL unread notification rows for the user, "
            "independent of the returned page size. Clients should prefer "
            "this over deriving a badge from the (possibly truncated) page. "
            "Older clients that ignore the field are unaffected."
        ),
    )
