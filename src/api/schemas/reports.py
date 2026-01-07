from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List


class MessageReportRequest(BaseModel):
    """Report a message."""

    message_id: str = Field(..., description="ID of the message to report")
    channel_id: str = Field(..., description="Channel containing the message")
    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for report"
    )
    category: str = Field("other", description="Report category")
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    server_id: Optional[str] = Field(None, description="Server ID if applicable")


class UserReportRequest(BaseModel):
    """Report a user."""

    user_id: str = Field(..., description="ID of the user to report")
    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for report"
    )
    category: str = Field("other", description="Report category")
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    evidence_message_ids: Optional[List[str]] = Field(
        None, description="Message IDs as evidence"
    )


class ReportResponse(BaseModel):
    """Response for report submission."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether report was successful")
    report_id: str = Field(..., description="ID of the created report")
    message: str = Field(..., description="Status message")
