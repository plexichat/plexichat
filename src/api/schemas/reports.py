from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Literal, Optional, List

from .common import SnowflakeID, reject_unsafe_ui_text

#: Categories the report store knows how to bucket (``ReportCategory``). The
#: routes already reject anything else at runtime; declaring the set here moves
#: that rejection to validation so a bogus category cannot enter the pipeline.
ReportCategoryValue = Literal[
    "harassment",
    "spam",
    "inappropriate",
    "illegal",
    "hate_speech",
    "threats",
    "impersonation",
    "other",
]


class MessageReportRequest(BaseModel):
    """Report a message."""

    message_id: SnowflakeID = Field(..., description="ID of the message to report")
    channel_id: SnowflakeID = Field(..., description="Channel containing the message")
    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for report"
    )
    category: ReportCategoryValue = Field("other", description="Report category")
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    server_id: Optional[SnowflakeID] = Field(
        None, description="Server ID if applicable"
    )

    @field_validator("reason", "details")
    @classmethod
    def _no_ui_xss_chars(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return value
        return reject_unsafe_ui_text(value, info.field_name)


class UserReportRequest(BaseModel):
    """Report a user."""

    user_id: SnowflakeID = Field(..., description="ID of the user to report")
    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for report"
    )
    category: ReportCategoryValue = Field("other", description="Report category")
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    evidence_message_ids: Optional[List[SnowflakeID]] = Field(
        None, max_length=20, description="Message IDs as evidence"
    )

    @field_validator("reason", "details")
    @classmethod
    def _no_ui_xss_chars(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return value
        return reject_unsafe_ui_text(value, info.field_name)


class ReportResponse(BaseModel):
    """Response for report submission."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether report was successful")
    report_id: str = Field(..., description="ID of the created report")
    message: str = Field(..., description="Status message")
