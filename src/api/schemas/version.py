"""
Version and status schemas for API responses.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from enum import Enum


class ServerState(str, Enum):
    """Server operational states."""

    RUNNING = "running"
    MAINTENANCE = "maintenance"
    SHUTTING_DOWN = "shutting_down"
    RESTARTING = "restarting"


class VersionInfo(BaseModel):
    """Version information."""

    model_config = ConfigDict(from_attributes=True)

    stage: str = Field(
        ...,
        description="Version stage: a (alpha), b (beta), c (candidate), r (release)",
    )
    major: int = Field(..., description="Major version number")
    minor: int = Field(..., description="Minor version number")
    build: int = Field(..., description="Build number")
    string: str = Field(..., description="Full version string (e.g., 'a.1.0-1')")


class ServerVersionResponse(BaseModel):
    """Server version information response."""

    model_config = ConfigDict(from_attributes=True)

    version: VersionInfo
    min_supported_version: Optional[VersionInfo] = Field(
        None, description="Minimum client version supported"
    )
    api_version: str = Field(..., description="API version prefix (e.g., 'v1')")


class VersionNegotiateRequest(BaseModel):
    """Client version negotiation request."""

    model_config = ConfigDict(from_attributes=True)

    client_version: str = Field(..., description="Client's current version string")
    supported_versions: Optional[List[str]] = Field(
        None, description="List of server versions client supports"
    )


class VersionNegotiateResponse(BaseModel):
    """Version negotiation response."""

    model_config = ConfigDict(from_attributes=True)

    compatible: bool = Field(
        ..., description="Whether client is compatible with server"
    )
    server_version: VersionInfo
    client_version: VersionInfo
    min_supported_version: Optional[VersionInfo] = None
    update_required: bool = Field(False, description="Client must update to continue")
    update_recommended: bool = Field(False, description="Client should update soon")
    message: Optional[str] = Field(None, description="Human-readable status message")
    update_url: Optional[str] = Field(None, description="URL to download client update")


class ServerStatusResponse(BaseModel):
    """Server status response."""

    model_config = ConfigDict(from_attributes=True)

    state: ServerState = Field(..., description="Current server state")
    version: VersionInfo
    uptime_seconds: Optional[int] = Field(None, description="Server uptime in seconds")
    maintenance_message: Optional[str] = Field(
        None, description="Maintenance announcement"
    )
    estimated_downtime_seconds: Optional[int] = Field(
        None, description="Estimated downtime if shutting down"
    )
    restart_at: Optional[str] = Field(
        None, description="ISO timestamp of scheduled restart"
    )


class VersionErrorDetail(BaseModel):
    """Version-related error details."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    client_version: Optional[str] = Field(None, description="Client's version")
    min_version: Optional[str] = Field(None, description="Minimum required version")
    server_version: Optional[str] = Field(None, description="Current server version")
    update_url: Optional[str] = Field(None, description="URL to download update")


class VersionErrorResponse(BaseModel):
    """Version error response wrapper."""

    model_config = ConfigDict(from_attributes=True)

    error: VersionErrorDetail
