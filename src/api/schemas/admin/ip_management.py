"""
IP management schemas.
"""

from typing import Optional
from pydantic import BaseModel, Field


class IPBlockRequest(BaseModel):
    """Request to block an IP address."""

    ip_address: str = Field(..., description="IP address to block")
    reason: Optional[str] = Field(
        None, max_length=500, description="Reason for blocking"
    )
    duration_hours: Optional[int] = Field(None, description="Duration in hours")


class BlockedIPResponse(BaseModel):
    """Blocked IP information."""

    ip_address: str
    reason: Optional[str]
    blocked_at: int
    # The blocking admin's id is a snowflake; emit it as a string so a JS
    # Number cannot round it (see admin roles / access-token responses).
    blocked_by: Optional[str]
    expires_at: Optional[int]
