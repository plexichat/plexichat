"""
Version negotiation and server status endpoints.
"""

import time
from fastapi import APIRouter, HTTPException
from typing import Optional

import utils.version as version_util
from utils.version import InvalidVersionError

from ..schemas.version import (
    ServerVersionResponse,
    VersionNegotiateRequest,
    VersionNegotiateResponse,
    ServerStatusResponse,
    ServerState,
    VersionInfo,
    VersionErrorResponse,
    VersionErrorDetail,
)

router = APIRouter(tags=["Version"])

# Server start time for uptime calculation
_server_start_time: float = time.time()

# Server state (can be updated by admin/system)
_server_state: ServerState = ServerState.RUNNING
_maintenance_message: Optional[str] = None
_estimated_downtime: Optional[int] = None
_restart_at: Optional[str] = None
_update_url: Optional[str] = None


def _version_to_info(ver) -> VersionInfo:
    """Convert Version object to VersionInfo schema."""
    return VersionInfo(
        stage=ver.stage.value,
        major=ver.major,
        minor=ver.minor,
        build=ver.build,
        string=version_util.format_version(ver),
    )


@router.get("/version", response_model=ServerVersionResponse)
async def get_server_version():
    """
    Get server version information.
    
    Returns the current server version and minimum supported client version.
    Clients should call this on startup to verify compatibility.
    """
    current = version_util.current()
    min_supported = version_util.min_supported()
    
    return ServerVersionResponse(
        version=_version_to_info(current),
        min_supported_version=_version_to_info(min_supported) if min_supported else None,
        api_version="v1",
    )


@router.post(
    "/version/negotiate",
    response_model=VersionNegotiateResponse,
    responses={
        400: {"model": VersionErrorResponse, "description": "Invalid version format"},
        426: {"model": VersionErrorResponse, "description": "Client update required"},
    },
)
async def negotiate_version(request: VersionNegotiateRequest):
    """
    Negotiate version compatibility with the server.
    
    Clients should call this endpoint to check if their version is compatible.
    The server will indicate if an update is required or recommended.
    """
    try:
        client_ver = version_util.parse(request.client_version)
    except InvalidVersionError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_VERSION_FORMAT",
                    "message": str(e),
                    "client_version": request.client_version,
                }
            },
        )
    
    server_ver = version_util.current()
    min_supported = version_util.min_supported()
    
    # Check compatibility
    is_compatible = version_util.is_client_compatible(request.client_version)
    
    # Determine if update is recommended (client is older than server)
    update_recommended = False
    if is_compatible:
        comparison = version_util.compare(
            request.client_version,
            version_util.current_string()
        )
        update_recommended = comparison < 0
    
    response = VersionNegotiateResponse(
        compatible=is_compatible,
        server_version=_version_to_info(server_ver),
        client_version=_version_to_info(client_ver),
        min_supported_version=_version_to_info(min_supported) if min_supported else None,
        update_required=not is_compatible,
        update_recommended=update_recommended,
        update_url=_update_url,
    )
    
    if not is_compatible:
        response.message = (
            f"Client version {request.client_version} is no longer supported. "
            f"Please update to at least {version_util.format_version(min_supported)}."
        )
        raise HTTPException(
            status_code=426,  # Upgrade Required
            detail={
                "error": {
                    "code": "VERSION_OUTDATED",
                    "message": response.message,
                    "client_version": request.client_version,
                    "min_version": version_util.format_version(min_supported) if min_supported else None,
                    "server_version": version_util.current_string(),
                    "update_url": _update_url,
                }
            },
        )
    
    if update_recommended:
        response.message = (
            f"A newer version ({version_util.current_string()}) is available. "
            "Consider updating for the latest features and fixes."
        )
    else:
        response.message = "Client version is compatible."
    
    return response


@router.get("/status", response_model=ServerStatusResponse)
async def get_server_status():
    """
    Get current server status.
    
    Returns the server's operational state, version, and any maintenance announcements.
    Clients should poll this endpoint periodically to stay informed of server state changes.
    
    Recommended polling interval: 60 seconds during normal operation,
    5 seconds when state is not 'running'.
    """
    current = version_util.current()
    uptime = int(time.time() - _server_start_time)
    
    return ServerStatusResponse(
        state=_server_state,
        version=_version_to_info(current),
        uptime_seconds=uptime,
        maintenance_message=_maintenance_message,
        estimated_downtime_seconds=_estimated_downtime,
        restart_at=_restart_at,
    )


# Internal functions for server state management (called by system/admin)

def set_server_state(
    state: ServerState,
    message: Optional[str] = None,
    estimated_downtime: Optional[int] = None,
    restart_at: Optional[str] = None,
):
    """
    Set the server state (internal use).
    
    Args:
        state: New server state
        message: Optional maintenance/status message
        estimated_downtime: Estimated downtime in seconds
        restart_at: ISO timestamp of scheduled restart
    """
    global _server_state, _maintenance_message, _estimated_downtime, _restart_at
    _server_state = state
    _maintenance_message = message
    _estimated_downtime = estimated_downtime
    _restart_at = restart_at


def set_update_url(url: Optional[str]):
    """Set the client update URL."""
    global _update_url
    _update_url = url


def get_server_state() -> ServerState:
    """Get current server state."""
    return _server_state
