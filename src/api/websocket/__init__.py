"""
WebSocket Gateway Module - WebSocket gateway for PlexiChat.

Provides real-time event delivery to connected clients.

Usage:
    from src.api.websocket import setup, get_router

    # Setup gateway
    setup(auth_module=auth, events_module=events)

    # Add to FastAPI app
    app.include_router(get_router())
"""

from typing import Optional

from .opcodes import GatewayOpcode, GatewayCloseCode
from .connection import Connection, ConnectionState
from .session import SessionManager
from .dispatcher import GatewayDispatcher

__all__ = [
    "setup",
    "get_router",
    "get_session_manager",
    "get_dispatcher",
    "is_setup",
    "broadcast_server_status",
    "close_all_connections",
    "GatewayOpcode",
    "GatewayCloseCode",
    "Connection",
    "ConnectionState",
    "SessionManager",
    "GatewayDispatcher",
]

_session_manager: Optional[SessionManager] = None
_dispatcher: Optional[GatewayDispatcher] = None
_auth_module = None
_events_module = None
_presence_module = None
_servers_module = None
_setup_complete = False


def setup(
    auth_module=None,
    events_module=None,
    presence_module=None,
    servers_module=None,
    heartbeat_interval_ms: int = 45000,
    session_timeout_ms: int = 60000,
    max_connections_per_user: int = 5,
    rate_limit_per_minute: int = 120,
) -> None:
    """
    Initialize the WebSocket gateway module.

    Args:
        auth_module: Auth module for token verification
        events_module: Events module for event subscription
        presence_module: Presence module for status updates
        servers_module: Servers module for guild data
        heartbeat_interval_ms: Heartbeat interval in milliseconds
        session_timeout_ms: Session timeout for resume
        max_connections_per_user: Max concurrent connections per user
        rate_limit_per_minute: Max events per minute per connection
    """
    global _session_manager, _dispatcher, _auth_module, _events_module
    global _presence_module, _servers_module, _setup_complete

    _auth_module = auth_module
    _events_module = events_module
    _presence_module = presence_module
    _servers_module = servers_module

    _session_manager = SessionManager(
        heartbeat_interval_ms=heartbeat_interval_ms,
        session_timeout_ms=session_timeout_ms,
        max_connections_per_user=max_connections_per_user,
    )

    _dispatcher = GatewayDispatcher(
        session_manager=_session_manager,
        events_module=_events_module,
        rate_limit_per_minute=rate_limit_per_minute,
    )

    if _events_module:
        _events_module.subscribe(_dispatcher.on_event)

    _setup_complete = True


def _ensure_setup() -> None:
    """Ensure module is set up before use."""
    if not _setup_complete:
        raise RuntimeError(
            "WebSocket gateway not initialized. Call websocket.setup() first."
        )


def get_router():
    """Get the FastAPI router for the gateway endpoint."""
    _ensure_setup()
    from .gateway import router
    return router


def get_session_manager() -> SessionManager:
    """Get the session manager instance."""
    _ensure_setup()
    assert _session_manager is not None  # Ensured by _ensure_setup
    return _session_manager


def get_dispatcher() -> GatewayDispatcher:
    """Get the gateway dispatcher instance."""
    _ensure_setup()
    assert _dispatcher is not None  # Ensured by _ensure_setup
    return _dispatcher


def get_auth_module():
    """Get the auth module."""
    _ensure_setup()
    return _auth_module


def get_events_module():
    """Get the events module."""
    _ensure_setup()
    return _events_module


def get_presence_module():
    """Get the presence module."""
    _ensure_setup()
    return _presence_module


def get_servers_module():
    """Get the servers module."""
    _ensure_setup()
    return _servers_module


def is_setup() -> bool:
    """Check if the gateway module is initialized."""
    return _setup_complete


async def broadcast_server_status(status_data: dict) -> int:
    """
    Broadcast server status to all connected clients.
    
    Convenience function that delegates to the dispatcher.
    
    Args:
        status_data: Status information containing:
            - state: "shutting_down", "restarting", "maintenance"
            - message: Human-readable message
            - estimated_downtime_seconds: Optional estimated downtime

    Returns:
        Number of connections notified
    """
    if not _setup_complete or _dispatcher is None:
        return 0
    return await _dispatcher.broadcast_server_status(status_data)


async def close_all_connections(
    close_code: int = 4017,
    reason: str = "Server shutting down",
    notify_first: bool = True,
    grace_period_seconds: float = 2.0,
) -> int:
    """
    Gracefully close all WebSocket connections.
    
    Args:
        close_code: WebSocket close code (default: SERVER_SHUTDOWN = 4017)
        reason: Close reason message
        notify_first: Whether to send SERVER_STATUS before closing
        grace_period_seconds: Time to wait after notification before closing

    Returns:
        Number of connections closed
    """
    if not _setup_complete or _dispatcher is None:
        return 0
    return await _dispatcher.close_all_connections(
        close_code=close_code,
        reason=reason,
        notify_first=notify_first,
        grace_period_seconds=grace_period_seconds,
    )
