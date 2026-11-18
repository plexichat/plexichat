"""
Version negotiation and server status endpoints.
"""

import time
from fastapi import APIRouter, HTTPException, status
from typing import Optional

import utils.version as version_util
from utils.version import InvalidVersionError
import utils.logger as logger
from src.core.security.hardening.text import strip_control_characters

from ..schemas.version import (
    ServerVersionResponse,
    VersionNegotiateRequest,
    VersionNegotiateResponse,
    ServerStatusResponse,
    ServerState,
    VersionInfo,
    VersionErrorResponse,
)

router = APIRouter(tags=["Version"])

# Bound attacker-controlled version input. The value is echoed back in
# negotiation error responses, so an unlimited string would also produce an
# unlimited reflected response (and unbounded work in the PEP 440 parser).
MAX_CLIENT_VERSION_LENGTH = 128

# Server start time for uptime calculation
_server_start_time: float = time.time()

# Server state (can be updated by admin/system)
_server_state: ServerState = ServerState.RUNNING
_maintenance_message: Optional[str] = None
_estimated_downtime: Optional[int] = None
_restart_at: Optional[str] = None
_update_url: Optional[str] = None

# Cached *server* version info for fast status responses. Only
# _server_version_info() writes here: _version_to_info() is a pure conversion
# used for the client's and minimum-supported versions too, and letting it
# write to this cache made /status report whichever version was converted last.
_cached_version_info: Optional[VersionInfo] = None

# The server version is fixed for the life of a process (``version.setup()``
# runs at boot). Build the /version payload once instead of paying the
# ``@cached`` decorator's Valkey get/set + serialization round trip on every
# cold cache (the decorator's hour-long TTL also went stale across upgrades).
_cached_server_version: Optional[ServerVersionResponse] = None


def _version_to_info(ver) -> VersionInfo:
    """Convert Version object to VersionInfo schema."""
    try:
        return VersionInfo(
            release=list(ver.release),
            pre=list(ver.pre) if ver.pre else None,
            post=ver.post,
            dev=ver.dev,
            local=ver.local,
            string=version_util.format_version(ver),
        )
    except Exception as e:
        logger.error(f"Error converting version object: {e}")
        return VersionInfo(
            release=[0, 0, 0],
            pre=None,
            post=None,
            dev=None,
            local=None,
            string="0.0.0",
        )


def _server_version_info() -> VersionInfo:
    """Return the server's own VersionInfo, memoised for fast status responses."""
    global _cached_version_info
    if _cached_version_info is None:
        _cached_version_info = _version_to_info(version_util.current())
    return _cached_version_info


@router.get(
    "/version", response_model=ServerVersionResponse, summary="Get server version"
)
def get_server_version() -> ServerVersionResponse:
    """
    Get server version information.

    Returns the current server version and minimum supported client version.
    Clients should call this on startup to verify compatibility.
    """
    global _cached_server_version
    if _cached_server_version is None:
        try:
            min_supported = version_util.min_supported()

            _cached_server_version = ServerVersionResponse(
                version=_server_version_info(),
                min_supported_version=_version_to_info(min_supported)
                if min_supported
                else None,
                api_version="v1",
            )
        except Exception as e:
            logger.error(f"Error in get_server_version: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    return _cached_server_version


@router.post(
    "/version/negotiate",
    response_model=VersionNegotiateResponse,
    summary="Negotiate version",
    responses={
        400: {"model": VersionErrorResponse, "description": "Invalid version format"},
        426: {"model": VersionErrorResponse, "description": "Client update required"},
        500: {"model": VersionErrorResponse, "description": "Internal server error"},
    },
)
def negotiate_version(
    request: VersionNegotiateRequest,
) -> VersionNegotiateResponse:
    """
    Negotiate version compatibility with the server.

    Clients should call this endpoint to check if their version is compatible.
    The server will indicate if an update is required or recommended.
    """
    # ``client_version`` is fully attacker-controlled and is echoed into both
    # logs and error responses. Bound it and strip C0 controls (newlines in
    # particular) before either use: a crafted value must not forge log lines
    # and the reflected response must stay bounded.
    raw_client_version = request.client_version
    if len(raw_client_version) > MAX_CLIENT_VERSION_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_VERSION_FORMAT",
                    "message": "Invalid version format",
                }
            },
        )
    client_version = strip_control_characters(raw_client_version)

    try:
        try:
            client_ver = version_util.parse(client_version)
        except InvalidVersionError:
            logger.warning("Invalid version format negotiated: %s", client_version)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "INVALID_VERSION_FORMAT",
                        "message": "Invalid version format",
                        "client_version": client_version,
                    }
                },
            )

        try:
            min_supported = version_util.min_supported()

            # Check compatibility
            is_compatible = version_util.is_client_compatible(client_version)

            # Determine if update is recommended (client is older than server)
            update_recommended = False
            if is_compatible:
                comparison = version_util.compare(
                    client_version, version_util.current_string()
                )
                update_recommended = comparison < 0

            message = None
            if not is_compatible:
                min_version_str = (
                    version_util.format_version(min_supported)
                    if min_supported
                    else "latest"
                )
                message = (
                    f"Client version {client_version} is no longer supported. "
                    f"Please update to at least {min_version_str}."
                )

            response = VersionNegotiateResponse(
                compatible=is_compatible,
                server_version=_server_version_info(),
                client_version=_version_to_info(client_ver),
                min_supported_version=_version_to_info(min_supported)
                if min_supported
                else None,
                update_required=not is_compatible,
                update_recommended=update_recommended,
                update_url=_update_url,
                message=message,
            )

            if not is_compatible:
                logger.info(
                    "Client version %s is outdated. Min supported: %s",
                    client_version,
                    version_util.format_version(min_supported)
                    if min_supported
                    else "N/A",
                )
                raise HTTPException(
                    status_code=status.HTTP_426_UPGRADE_REQUIRED,
                    detail={
                        "error": {
                            "code": "VERSION_OUTDATED",
                            "message": response.message,
                            "client_version": client_version,
                            "min_version": version_util.format_version(min_supported)
                            if min_supported is not None
                            else None,
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
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error in negotiate_version business logic: {e}", exc_info=True
            )
            # Never return the raw exception string: internal errors routinely
            # carry file paths, connection strings or other implementation
            # details (CWE-209). The full exception stays in the server log.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": "INTERNAL_SERVER_ERROR",
                        "message": "Internal server error",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in negotiate_version: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Internal server error",
                }
            },
        )


@router.get("/status", response_model=ServerStatusResponse, summary="Get server status")
def get_server_status() -> ServerStatusResponse:
    """
    Get current server status.

    Returns the server's operational state, version, and any maintenance announcements.
    Clients should poll this endpoint periodically to stay informed of server state changes.

    Recommended polling interval: 60 seconds during normal operation,
    5 seconds when state is not 'running'.
    """
    # Uptime advances every second, so a 10s cross-process cache (the old
    # ``@cached(ttl=10, prefix="api_status")``) both rounded uptime and paid a
    # Valkey round trip. Building the small response directly is cheaper and
    # keeps uptime accurate; the version payload it embeds is already memoised.
    try:
        ver_info = _server_version_info()

        uptime = int(time.time() - _server_start_time)

        return ServerStatusResponse(
            state=_server_state,
            version=ver_info,
            uptime_seconds=uptime,
            maintenance_message=_maintenance_message,
            estimated_downtime_seconds=_estimated_downtime,
            restart_at=_restart_at,
        )
    except Exception as e:
        logger.error(f"Error in get_server_status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
    # /status is no longer served from the Valkey ``api_status*`` cache, so
    # there is nothing to invalidate; the next call reads these values directly.


def set_update_url(url: Optional[str]):
    """Set the client update URL."""
    global _update_url
    _update_url = url


def get_server_state() -> ServerState:
    """Get current server state."""
    return _server_state
