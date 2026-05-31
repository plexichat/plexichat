"""
Support ticket schemas.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class TicketStatusUpdate(BaseModel):
    """Update ticket status."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(..., pattern="^(open|in_progress|resolved|closed)$")


class InternalNoteCreate(BaseModel):
    """Create internal note."""

    model_config = ConfigDict(from_attributes=True)

    content: str = Field(..., min_length=1, max_length=2000)


class TicketResponse(BaseModel):
    """Feedback ticket response."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Ticket ID")
    user_id: str = Field(..., description="User ID who submitted feedback")
    username: str = Field(..., description="Username who submitted feedback")
    content: str = Field(..., description="Feedback content")
    category: Optional[str] = Field(None, description="Feedback category")
    rating: Optional[int] = Field(None, description="Feedback rating (1-5)")
    status: str = Field(..., description="Ticket status")
    created_at: int = Field(..., description="Creation timestamp")
    resolved_at: Optional[int] = Field(None, description="Resolution timestamp")
    resolved_by: Optional[str] = Field(None, description="Admin ID who resolved it")


class NoteResponse(BaseModel):
    """Admin note response."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Note ID")
    ticket_id: str = Field(..., description="Ticket ID")
    admin_id: str = Field(..., description="Admin ID who created the note")
    admin_username: str = Field(..., description="Admin username")
    content: str = Field(..., description="Note content")
    created_at: int = Field(..., description="Creation timestamp")
