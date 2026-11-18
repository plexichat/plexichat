"""
Version and status schemas for API responses.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Annotated, Optional, List, Union
from enum import Enum

# Version-negotiation input is fully attacker-controlled and is parsed by the
# PEP 440 helper. The body cap (10 MB) is not a field bound: a single string can
# stay under it while still being far larger than any real version, and the
# ``supported_versions`` list can hold thousands of entries. Bound both at the
# schema so a hostile value is rejected before any parsing work is done.
#
# ``MAX_CLIENT_VERSION_LENGTH`` mirrors the route guard in
# ``src.api.routes.version``; the invariant suite asserts they never drift.
MAX_CLIENT_VERSION_LENGTH = 128
MAX_SUPPORTED_VERSIONS = 16
MAX_SUPPORTED_VERSION_LENGTH = 128


class ServerState(str, Enum):
    """Server operational states."""

    RUNNING = "running"
    MAINTENANCE = "maintenance"
    SHUTTING_DOWN = "shutting_down"
    RESTARTING = "restarting"


class VersionInfo(BaseModel):
    """Version information following PEP 440."""

    model_config = ConfigDict(from_attributes=True)

    release: List[int] = Field(..., description="Release segment (e.g. [1, 0, 0])")
    pre: Optional[List[Union[str, int]]] = Field(
        None, description="Pre-release segment (e.g. ['a', 184]) or None"
    )
    post: Optional[int] = Field(
        None, description="Post-release segment (e.g. 1) or None"
    )
    dev: Optional[int] = Field(
        None, description="Development release segment (e.g. None)"
    )
    local: Optional[str] = Field(None, description="Local version segment (e.g. None)")
    string: str = Field(
        ..., description="Full canonical PEP 440 version string (e.g., '1.0.0a212')"
    )


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

    client_version: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CLIENT_VERSION_LENGTH,
        description="Client's current version string",
    )
    supported_versions: Optional[
        List[Annotated[str, Field(max_length=MAX_SUPPORTED_VERSION_LENGTH)]]
    ] = Field(
        None,
        max_length=MAX_SUPPORTED_VERSIONS,
        description="List of server versions client supports",
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

    code: Union[str, int] = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    client_version: Optional[str] = Field(None, description="Client's version")
    min_version: Optional[str] = Field(None, description="Minimum required version")
    server_version: Optional[str] = Field(None, description="Current server version")
    update_url: Optional[str] = Field(None, description="URL to download update")


class VersionErrorResponse(BaseModel):
    """Version error response wrapper."""

    model_config = ConfigDict(from_attributes=True)

    error: VersionErrorDetail
