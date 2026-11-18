"""
DSAR (Data Subject Access Request) schemas - Request/response models for data export endpoints.
"""

from typing import Literal, Optional, List
from pydantic import BaseModel, Field, ConfigDict

from src.api.schemas.common import SnowflakeID
from .auth import MAX_PASSWORD_INPUT_LENGTH


class DSARRequestBody(BaseModel):
    """DSAR data export request body."""

    model_config = ConfigDict(from_attributes=True)

    # This value is passed straight to ``verify_password`` (a synchronous
    # peppered-Argon2 verification that pre-hashes the whole input with HMAC).
    # Every other password-bearing request schema is bounded at the boundary;
    # without the same ceiling here a 10 MB body (the configured request cap)
    # reaches the hasher and blocks the event loop. Keep the single source of
    # truth shared with the auth schemas so the two cannot drift apart.
    password: str = Field(
        ...,
        max_length=MAX_PASSWORD_INPUT_LENGTH,
        description="Current password for verification",
    )
    format: Optional[Literal["json", "zip"]] = Field(
        default="json", description="Export format ('json' or 'zip')"
    )


class DSARRequestResponse(BaseModel):
    """DSAR request response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Request ID")
    status: str = Field(..., description="Request status")
    requested_at: int = Field(..., description="Request timestamp")
    completed_at: Optional[int] = Field(None, description="Completion timestamp")
    expires_at: Optional[int] = Field(None, description="Expiration timestamp")
    format: str = Field(..., description="Export format")
    file_size_bytes: Optional[int] = Field(None, description="File size in bytes")
    checksum: Optional[str] = Field(None, description="File checksum")


class DSARRequestListResponse(BaseModel):
    """List of DSAR requests response."""

    model_config = ConfigDict(from_attributes=True)

    requests: List[DSARRequestResponse] = Field(
        default_factory=list, description="List of DSAR requests"
    )


class DSARDownloadResponse(BaseModel):
    """DSAR download response."""

    model_config = ConfigDict(from_attributes=True)

    download_url: str = Field(..., description="URL to download the export")
    expires_at: int = Field(..., description="Download URL expiration timestamp")
    checksum: str = Field(..., description="File checksum for verification")
    file_size_bytes: int = Field(..., description="File size in bytes")
    format: str = Field(..., description="Export format")
