"""
Feedback API schemas.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class FeedbackCreate(BaseModel):
    """Feedback submission model."""
    model_config = ConfigDict(from_attributes=True)

    content: str = Field(..., min_length=10, max_length=2000, description="Feedback content")
    category: Optional[str] = Field(None, max_length=50, description="Feedback category")
    rating: Optional[int] = Field(None, ge=1, le=5, description="Feedback rating (1-5)")


class FeedbackResponse(BaseModel):
    """Feedback response model."""
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Feedback ID")
    message: str = Field(..., description="Success message")


class FeedbackStatusResponse(BaseModel):
    """Feedback status for current user."""
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = Field(..., description="Whether feedback system is enabled")
    submissions_this_hour: int = Field(..., description="Submissions in the last hour")
    submissions_today: int = Field(..., description="Submissions in the last 24 hours")
    max_per_hour: int = Field(..., description="Maximum submissions allowed per hour")
    max_per_day: int = Field(..., description="Maximum submissions allowed per day")
    can_submit: bool = Field(..., description="Whether user can currently submit feedback")
