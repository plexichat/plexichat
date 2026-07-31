"""
WebSocket Gateway Module - WebSocket gateway for Plexichat.

Provides real-time event delivery to connected clients.

Usage:
    from src.api.websocket import setup, get_router

    # Setup gateway
    setup(auth_module=auth, events_module=events)

    # Add to FastAPI app
    app.include_router(get_router())
"""

import asyncio
import json
import math
import threading
from typing import Any, Optional

import utils.logger as logger

from .opcodes import GatewayOpcode, GatewayCloseCode
from .connection import Connection, ConnectionState
from .session import SessionManager
from .dispatcher import GatewayDispatcher
from src.utils.system_alerts import record_system_alert as _record_system_alert

__all__ = [
    "setup",
    "get_router",
    "get_session_manager",
    "get_dispatcher",
    "is_setup",
    "broadcast_server_status",
    "broadcast_ratchet_update",
    "close_all_connections",
    "start_cross_worker_listener",
    "stop_cross_worker_listener",
    "cross_worker_listener_status",
    "get_poll_tuning",
    "update_poll_tuning",
    "GatewayOpcode",
    "GatewayCloseCode",
    "Connection",
    "ConnectionState",
    "SessionManager",
    "GatewayDispatcher",
]

# Valkey pub/sub channel name — must match the constant in
# ``src.core.events.manager``.
_CROSS_WORKER_CHANNEL = "events:cross_worker"

_session_manager: Optional[SessionManager] = None
_dispatcher: Optional[GatewayDispatcher] = None
_auth_module = None
_events_module = None
_presence_module = None
_servers_module = None
_setup_complete = False

# Cross-worker listener state
_cross_worker_task: Optional[asyncio.Task] = None
_cross_worker_stop: threading.Event = threading.Event()

# Tunable constants for the cross-worker poll loop (exposed at module
# level so chaos tests can shrink them without patching function locals).
_POLL_INTERVAL_SEC: float = 0.1
_HEARTBEAT_INTERVAL_POLLS: int = 300
_MAX_BACKOFF_SEC: float = 60.0
_BACKOFF_RESET_AFTER_POLLS: int = 600  # ~60 s at 100 ms poll interval
_INITIAL_BACKOFF_SEC: float = 1.0
_GLIDE_REQUEST_TIMEOUT_MS: int = 5000
_HEARTBEAT_ALERT_INTERVAL_SEC: float = 300.0  # record a heartbeat alert every 5 min

# Observable listener health — updated by the poll loop, read by /health.
_listener_status: str = "disabled"
_listener_backoff_sec: float = 1.0
_listener_poll_count: int = 0
_listener_lock: threading.Lock = threading.Lock()


def _update_listener_status(
    status: str,
    backoff_sec: Optional[float] = None,
    poll_count: Optional[int] = None,
) -> None:
    """Atomically update the listener-health globals."""
    global _listener_status, _listener_backoff_sec, _listener_poll_count
    with _listener_lock:
        _listener_status = status
        if backoff_sec is not None:
            _listener_backoff_sec = backoff_sec
        if poll_count is not None:
            _listener_poll_count = poll_count


def cross_worker_listener_status() -> dict:
    """Return observable health metrics for the cross-worker listener.

    Safe to call from any thread.  Returns a dict with keys
    ``enabled``, ``status``, ``backoff_sec``, ``poll_count``, and
    ``running`` (bool).

    When Valkey is disabled ``enabled`` is ``false`` and other
    fields are omitted.
    """
    import utils.config as _cfg

    valkey_cfg = _cfg.get("redis") or {}
    if not valkey_cfg.get("enabled", False):
        return {"enabled": False}

    with _listener_lock:
        return {
            "enabled": True,
            "status": _listener_status,
            "backoff_sec": _listener_backoff_sec,
            "poll_count": _listener_poll_count,
            "running": _cross_worker_task is not None and not _cross_worker_task.done(),
            "worker_id": _get_worker_id(),
            "tuning": {
                "poll_interval_sec": _POLL_INTERVAL_SEC,
                "heartbeat_interval_polls": _HEARTBEAT_INTERVAL_POLLS,
                "max_backoff_sec": _MAX_BACKOFF_SEC,
                "backoff_reset_after_polls": _BACKOFF_RESET_AFTER_POLLS,
                "initial_backoff_sec": _INITIAL_BACKOFF_SEC,
                "glide_request_timeout_ms": _GLIDE_REQUEST_TIMEOUT_MS,
                "heartbeat_alert_interval_sec": _HEARTBEAT_ALERT_INTERVAL_SEC,
            },
        }


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
        _events_module.subscribe(_dispatcher.on_event, critical=True)

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
    assert _session_manager is not None
    return _session_manager


def get_dispatcher() -> GatewayDispatcher:
    """Get the gateway dispatcher instance."""
    _ensure_setup()
    assert _dispatcher is not None
    return _dispatcher


def get_auth_module():
    """Get the auth module."""
    _ensure_setup()
    return _auth_module


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
    """Broadcast server status to all connected clients."""
    if not _setup_complete or _dispatcher is None:
        return 0
    return await _dispatcher.broadcast_server_status(status_data)


async def broadcast_ratchet_update(
    conversation_id: int,
    update_data: dict,
) -> int:
    """Broadcast a RATCHET_UPDATE to all connected clients."""
    if not _setup_complete or _dispatcher is None:
        return 0
    return await _dispatcher.broadcast_ratchet_update(
        conversation_id=conversation_id,
        update_data=update_data,
    )


async def close_all_connections(
    close_code: int = 4017,
    reason: str = "Server shutting down",
    notify_first: bool = True,
    grace_period_seconds: float = 2.0,
) -> int:
    """Gracefully close all WebSocket connections."""
    if not _setup_complete or _dispatcher is None:
        return 0
    return await _dispatcher.close_all_connections(
        close_code=close_code,
        reason=reason,
        notify_first=notify_first,
        grace_period_seconds=grace_period_seconds,
    )


# ---------------------------------------------------------------------------
# Cross-worker event listener (Valkey pub/sub → local WebSocket dispatch)
# ---------------------------------------------------------------------------


def _get_worker_id() -> str:
    """Return this worker's identity from the session manager."""
    if _session_manager is not None:
        return _session_manager.worker_id
    return "unknown"


async def _handle_cross_worker_message(raw_message: str) -> None:
    """Parse a cross-worker event and dispatch it to local connections."""
    try:
        payload = json.loads(raw_message)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Cross-worker: invalid JSON payload")
        return

    source_worker = payload.get("sw", "")
    if source_worker == _get_worker_id():
        return

    event_type: str = payload.get("t", "")
    event_data: dict = payload.get("d", {})
    user_ids: list = payload.get("u", [])

    if not event_type or not user_ids:
        return

    if _dispatcher is not None:
        try:
            await _dispatcher.dispatch_remote_event(event_type, event_data, user_ids)
        except Exception:
            logger.debug(
                "Failed to dispatch remote event %s to %d users",
                event_type,
                len(user_ids),
            )


async def _cross_worker_poll_loop() -> None:
    """Background asyncio task: poll a **dedicated** Valkey pub/sub
    connection for cross-worker events.

    A separate GlideClient is created so subscribing does not put the
    shared client into subscriber mode.

    If the dedicated connection drops, the outer retry loop reconnects
    with exponential backoff (1 s → 60 s cap).  Backoff resets after
    ~60 s of healthy polls.
    """
    import utils.config as config

    valkey_cfg = config.get("redis") or {}
    if not valkey_cfg.get("enabled", False):
        logger.info("Cross-worker listener: Valkey disabled — skipping")
        return

    key_prefix = valkey_cfg.get("key_prefix", "plexichat:")
    full_channel = f"{key_prefix}{_CROSS_WORKER_CHANNEL}"

    backoff_sec = _INITIAL_BACKOFF_SEC
    dead_recorded = False  # only fire one "dead" event per reconnection cycle

    async def _delay_and_backoff() -> None:
        """Sleep for ``backoff_sec`` then double it (capped).

        When backoff reaches the cap the worker is effectively dead
        — record a ``dead`` event so operators can investigate.
        """
        nonlocal backoff_sec, dead_recorded
        prev = backoff_sec
        _update_listener_status("reconnecting", backoff_sec=backoff_sec)
        await asyncio.sleep(min(backoff_sec, _MAX_BACKOFF_SEC))
        backoff_sec = min(backoff_sec * 2, _MAX_BACKOFF_SEC)
        _update_listener_status("reconnecting", backoff_sec=backoff_sec)
        if prev >= _MAX_BACKOFF_SEC and backoff_sec >= _MAX_BACKOFF_SEC:
            if not dead_recorded:
                dead_recorded = True
                _record_system_alert(
                    "dead", {"backoff_sec": backoff_sec}, severity="error"
                )

    while not _cross_worker_stop.is_set():
        pubsub_client = _create_pubsub_client(valkey_cfg)
        if pubsub_client is None:
            await _delay_and_backoff()
            continue

        # Subscribe on the dedicated connection.
        try:
            pubsub_client.subscribe({full_channel})
            logger.info(
                "Cross-worker listener subscribed to channel '%s'",
                full_channel,
            )
            _update_listener_status("connected", backoff_sec=backoff_sec, poll_count=0)
            _record_system_alert(
                "connected", {"channel": full_channel, "backoff_sec": backoff_sec}
            )
            dead_recorded = False  # reset on successful reconnect
        except Exception as e:
            logger.warning("Cross-worker listener subscribe failed: %s", e)
            _close_pubsub_client(pubsub_client)
            await _delay_and_backoff()
            continue

        poll_count = 0
        healthy_polls = 0
        reconnect = False
        last_heartbeat = asyncio.get_event_loop().time()

        try:
            while not _cross_worker_stop.is_set():
                msg = pubsub_client.try_get_pubsub_message()
                if msg is not None:
                    try:
                        raw = msg.payload if hasattr(msg, "payload") else str(msg)
                        await _handle_cross_worker_message(raw)
                    except Exception:
                        logger.debug("Cross-worker: failed to handle message")
                else:
                    await asyncio.sleep(_POLL_INTERVAL_SEC)

                poll_count += 1
                healthy_polls += 1

                # Reset backoff after a sustained healthy stretch.
                if healthy_polls >= _BACKOFF_RESET_AFTER_POLLS:
                    backoff_sec = _INITIAL_BACKOFF_SEC
                    healthy_polls = 0

                # Record a periodic heartbeat alert so operators can see
                # healthy worker activity in the Alerts tab timeline.
                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= _HEARTBEAT_ALERT_INTERVAL_SEC:
                    last_heartbeat = now
                    _record_system_alert(
                        "heartbeat",
                        {"poll_count": poll_count, "backoff_sec": backoff_sec},
                    )

                if poll_count % _HEARTBEAT_INTERVAL_POLLS == 0:
                    _update_listener_status(
                        "connected",
                        backoff_sec=backoff_sec,
                        poll_count=poll_count,
                    )
                    logger.debug("Cross-worker listener alive (%d polls)", poll_count)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            reconnect = True
            _record_system_alert(
                "disconnected",
                {"error": str(e), "backoff_sec": backoff_sec, "poll_count": poll_count},
                severity="warning",
            )
            logger.warning(
                "Cross-worker listener disconnected: %s — reconnecting in %.1fs",
                e,
                min(backoff_sec, _MAX_BACKOFF_SEC),
            )
        finally:
            _close_pubsub_client(pubsub_client)

        if _cross_worker_stop.is_set():
            break
        if reconnect:
            _update_listener_status(
                "disconnected", backoff_sec=backoff_sec, poll_count=poll_count
            )
            await _delay_and_backoff()

    _update_listener_status("disconnected")
    _cross_worker_stop.clear()
    _record_system_alert("stopped", {"poll_count": -1})
    logger.info("Cross-worker listener stopped")


def _create_pubsub_client(valkey_cfg: dict) -> Any | None:
    """Create a standalone GlideClient just for pub/sub.

    Returns None if valkey-glide-sync is unavailable or the
    connection fails.
    """
    try:
        from glide_sync import (  # pyright: ignore[reportMissingImports]
            GlideClient,
            GlideClientConfiguration,
            NodeAddress,
            ServerCredentials,
        )
    except ImportError:
        logger.info("Cross-worker listener: valkey-glide-sync not available — skipping")
        return None

    host = valkey_cfg.get("host", "localhost")
    port = valkey_cfg.get("port", 6379)
    password = valkey_cfg.get("password", "") or None
    use_tls = valkey_cfg.get("ssl", False)
    db = valkey_cfg.get("db", 0)

    try:
        kwargs: dict = {
            "addresses": [NodeAddress(host, port)],
            "use_tls": use_tls,
        }
        if password:
            kwargs["credentials"] = ServerCredentials(password)
        kwargs["database_id"] = db
        kwargs["request_timeout"] = _GLIDE_REQUEST_TIMEOUT_MS

        glide_config = GlideClientConfiguration(**kwargs)
        client = GlideClient.create(glide_config)
        client.ping()
        return client
    except Exception as e:
        logger.warning("Cross-worker listener: dedicated client failed: %s", e)
        return None


def _close_pubsub_client(client: Any) -> None:
    """Close a dedicated pubsub client, swallowing all errors."""
    try:
        client.close()
    except Exception:
        pass


async def start_cross_worker_listener() -> None:
    """Start the background task that listens for cross-worker events."""
    global _cross_worker_task, _cross_worker_stop

    if not _setup_complete:
        logger.debug("Cross-worker listener: WebSocket not set up — skipping")
        return

    if _cross_worker_task is not None and not _cross_worker_task.done():
        logger.debug("Cross-worker listener already running")
        return

    _cross_worker_stop.clear()
    _update_listener_status("disconnected")
    _record_system_alert("started")
    _cross_worker_task = asyncio.create_task(_cross_worker_poll_loop())
    logger.info("Cross-worker listener started")


async def stop_cross_worker_listener() -> None:
    """Gracefully stop the cross-worker listener."""
    global _cross_worker_task, _cross_worker_stop

    _cross_worker_stop.set()

    task = _cross_worker_task
    if task is not None and not task.done():
        try:
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Cross-worker listener stop error: %s", e)
    _cross_worker_task = None
    _update_listener_status("disabled")
    logger.info("Cross-worker listener stopped")


def get_poll_tuning() -> dict:
    """Return the current poll-loop tuning constants.

    These values are read live from the running poll loop (via module
    globals) and can be changed at runtime with ``update_poll_tuning``.
    """
    return {
        "poll_interval_sec": _POLL_INTERVAL_SEC,
        "heartbeat_interval_polls": _HEARTBEAT_INTERVAL_POLLS,
        "max_backoff_sec": _MAX_BACKOFF_SEC,
        "backoff_reset_after_polls": _BACKOFF_RESET_AFTER_POLLS,
        "initial_backoff_sec": _INITIAL_BACKOFF_SEC,
        "glide_request_timeout_ms": _GLIDE_REQUEST_TIMEOUT_MS,
        "heartbeat_alert_interval_sec": _HEARTBEAT_ALERT_INTERVAL_SEC,
    }


def update_poll_tuning(updates: dict) -> dict:
    """Hot-reload poll-loop tuning constants at runtime.

    Only the keys present in ``updates`` are changed; omitted keys
    keep their current value.  Returns the full tuning dict after
    the update.  Validation: each value must be a positive number;
    invalid keys are silently ignored.

    Safe to call from any thread — C{*ylon} GIL makes assignments
    to these simple types atomic.
    """
    global _POLL_INTERVAL_SEC, _HEARTBEAT_INTERVAL_POLLS, _GLIDE_REQUEST_TIMEOUT_MS
    global _MAX_BACKOFF_SEC, _BACKOFF_RESET_AFTER_POLLS, _INITIAL_BACKOFF_SEC
    global _HEARTBEAT_ALERT_INTERVAL_SEC

    _ALLOWED_KEYS = {
        "poll_interval_sec",
        "heartbeat_interval_polls",
        "max_backoff_sec",
        "backoff_reset_after_polls",
        "initial_backoff_sec",
        "glide_request_timeout_ms",
        "heartbeat_alert_interval_sec",
    }

    _INT_KEYS = {
        "heartbeat_interval_polls",
        "backoff_reset_after_polls",
        "glide_request_timeout_ms",
    }

    for key, value in updates.items():
        if key not in _ALLOWED_KEYS:
            logger.debug("update_poll_tuning: unknown key '%s'", key)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            logger.debug("update_poll_tuning: invalid value for '%s': %s", key, value)
            continue
        if isinstance(value, float):
            if not math.isfinite(value) or value <= 0:
                logger.debug(
                    "update_poll_tuning: invalid value for '%s': %s", key, value
                )
                continue
            if key in _INT_KEYS and not value.is_integer():
                logger.debug(
                    "update_poll_tuning: integer value required for '%s': %s",
                    key,
                    value,
                )
                continue
        elif value <= 0:
            logger.debug("update_poll_tuning: invalid value for '%s': %s", key, value)
            continue

        if key == "poll_interval_sec":
            _POLL_INTERVAL_SEC = float(value)
        elif key == "heartbeat_interval_polls":
            _HEARTBEAT_INTERVAL_POLLS = int(value)
        elif key == "max_backoff_sec":
            _MAX_BACKOFF_SEC = float(value)
        elif key == "backoff_reset_after_polls":
            _BACKOFF_RESET_AFTER_POLLS = int(value)
        elif key == "initial_backoff_sec":
            _INITIAL_BACKOFF_SEC = float(value)
        elif key == "glide_request_timeout_ms":
            _GLIDE_REQUEST_TIMEOUT_MS = int(value)
        elif key == "heartbeat_alert_interval_sec":
            _HEARTBEAT_ALERT_INTERVAL_SEC = float(value)

        logger.info("Cross-worker tuning updated: %s = %s", key, value)

    return get_poll_tuning()
