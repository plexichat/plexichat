"""
DSAR (Data Subject Access Request) schemas - Request/response models for data export endpoints.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class DSARRequestBody(BaseModel):
    """DSAR data export request body."""

    model_config = ConfigDict(from_attributes=True)

    password: str = Field(..., description="Current password for verification")
    format: Optional[str] = Field(
        default="json", description="Export format ('json' or 'zip')"
    )


class DSARRequestResponse(BaseModel):
    """DSAR request response."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Request ID")
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
