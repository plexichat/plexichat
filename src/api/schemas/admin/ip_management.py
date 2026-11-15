"""
IP management schemas.
"""

from typing import Optional
import ipaddress

from pydantic import BaseModel, Field, field_validator


class IPBlockRequest(BaseModel):
    """Request to block an IP address."""

    ip_address: str = Field(..., description="IP address to block")
    reason: Optional[str] = Field(
        None, max_length=500, description="Reason for blocking"
    )
    duration_hours: Optional[int] = Field(
        None, ge=1, le=8760, description="Duration in hours (max one year)"
    )

    @field_validator("ip_address")
    @classmethod
    def _valid_ip(cls, value: str) -> str:
        try:
            ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError("ip_address must be a valid IPv4 or IPv6 address") from exc
        return value.strip()


class BlockedIPResponse(BaseModel):
    """Blocked IP information."""

    ip_address: str
    reason: Optional[str]
    blocked_at: int
    # The blocking admin's id is a snowflake; emit it as a string so a JS
    # Number cannot round it (see admin roles / access-token responses).
    blocked_by: Optional[str]
    expires_at: Optional[int]
