# pyright: reportAttributeAccessIssue=false
import os
import signal
import asyncio
import secrets
import threading
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.database import Database

import utils.logger as logger
import utils.config as config
import utils.version as version

VERSION = "a.1.0-63"


class PlexichatServer:
    """Main server class with lifecycle management."""

    def __init__(self):
        self.app = None
        self.server = None
        self.db: Optional["Database"] = None
        self.shutdown_event = threading.Event()
        self.restart_requested = False
        self._modules = {}
        self.worker_id = secrets.token_hex(4)

    def get_default_config(self) -> Dict[str, Any]:
        from src.config_defaults import get_default_config as fetch_default_config

        return fetch_default_config(version=VERSION)

    def validate_config(self) -> None:
        defaults = self.get_default_config()
        current = config.get_all()

        missing = []
        type_mismatches = []

        def check_recursive(d_dict, c_dict, path=""):
            for k, v in d_dict.items():
                current_path = f"{path}.{k}" if path else k
                if k not in c_dict:
                    missing.append(current_path)
                    continue

                if isinstance(v, dict) and isinstance(c_dict[k], dict):
                    check_recursive(v, c_dict[k], current_path)
                elif v is not None and c_dict[k] is not None:
                    if not isinstance(c_dict[k], type(v)):
                        type_mismatches.append(
                            f"{current_path}: expected {type(v).__name__}, got {type(c_dict[k]).__name__}"
                        )

        check_recursive(defaults, current)

        if missing:
            logger.warning(f"Missing configuration keys: {', '.join(missing)}")

        if type_mismatches:
            error_msg = f"Configuration type mismatches: {', '.join(type_mismatches)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    async def notify_clients_shutdown(self, message: str = "Server shutting down"):
        try:
            from src.api.routes.version import set_server_state
            from src.api.schemas.version import ServerState

            set_server_state(ServerState.SHUTTING_DOWN, message=message)
        except Exception as e:
            logger.debug(f"Could not update server shutdown state: {e}")

        try:
            from src.api import websocket

            if websocket.is_setup():
                await websocket.broadcast_server_status(
                    {"state": "shutting_down", "message": message, "restart_at": None}
                )
                logger.info("Notified clients of shutdown")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")

    async def notify_clients_restart(self, estimated_seconds: int = 10):
        try:
            from src.api.routes.version import set_server_state
            from src.api.schemas.version import ServerState

            set_server_state(
                ServerState.RESTARTING,
                message="Server is restarting",
                estimated_downtime=estimated_seconds,
            )
        except Exception as e:
            logger.debug(f"Could not update server restart state: {e}")

        try:
            from src.api import websocket

            if websocket.is_setup():
                await websocket.broadcast_server_status(
                    {
                        "state": "restarting",
                        "message": "Server is restarting",
                        "estimated_downtime_seconds": estimated_seconds,
                    }
                )
                logger.info("Notified clients of restart")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")

    def cleanup(self) -> None:
        logger.info("Cleaning up resources...")

        if not self.shutdown_event.is_set():
            self.shutdown_event.set()

        try:
            from src.api.routes.version import get_server_state, set_server_state
            from src.api.schemas.version import ServerState

            if get_server_state() == ServerState.RUNNING:
                set_server_state(
                    ServerState.SHUTTING_DOWN, message="Server shutting down"
                )
        except Exception as e:
            logger.debug(f"Could not update cleanup shutdown state: {e}")

        try:
            from src.api import websocket

            if websocket.is_setup():
                manager = websocket.get_session_manager()
                manager.clear_all_global_sessions()
        except Exception as e:
            logger.debug(f"Error cleaning up global sessions: {e}")

        if self.db:
            try:
                self.db.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")

        logger.info("Cleanup complete")

    def run(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        import uvicorn

        server_config = config.get("server", {})
        host = host or server_config.get("host", "127.0.0.1")
        port = port or server_config.get("port", 8000)

        ssl_config = {}
        tls_config = config.get("tls", {})
        use_https = False

        if tls_config.get("enabled", False) or tls_config.get(
            "auto_generate_self_signed", False
        ):
            try:
                from src.core import tls

                if tls.is_tls_enabled():
                    ssl_config = tls.get_tls_config()
                    if ssl_config:
                        use_https = True
                        logger.info("TLS enabled - server will use HTTPS")
            except ImportError:
                logger.warning("TLS module not available")
            except Exception as e:
                logger.error(f"Failed to configure TLS: {e}")

        assert self.app is not None
        assert host is not None
        assert port is not None

        log_level = config.get("logging", {}).get("level", "info").lower()

        uvi_config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level=log_level,
            loop="asyncio",
            **ssl_config,
        )

        self.server = uvicorn.Server(uvi_config)
        assert self.server is not None

        def signal_handler(signum, frame):
            signal_map = {
                signal.SIGINT: "SIGINT",
            }
            if hasattr(signal, "SIGTERM"):
                signal_map[signal.SIGTERM] = "SIGTERM"

            signal_name = signal_map.get(signum, f"signal {signum}")

            if self.shutdown_event.is_set():
                logger.warning(
                    f"Received second {signal_name}, forcing immediate exit..."
                )
                os._exit(1)

            logger.info(f"Received {signal_name}, initiating graceful shutdown...")
            self.shutdown_event.set()

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.notify_clients_shutdown(), loop)

            if self.server:
                self.server.should_exit = True

        signal.signal(signal.SIGINT, signal_handler)

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, signal_handler)

        logger.info("All modules initialized successfully")
        logger.info("=" * 60)

        ver = version.current_string()
        protocol = "https" if use_https else "http"
        server_line = f"Server: {protocol}://{host}:{port}"
        api_line = f"API:    {protocol}://{host}:{port}/api/v1"
        docs_line = f"Docs:   {protocol}://{host}:{port}/docs"
        tls_line = "TLS:    Enabled (self-signed)" if use_https else "TLS:    Disabled"

        print(f"""
+==============================================================+
|                    Plexichat API Server                      |
|                      Version {ver:<32}|
+==============================================================+
|  {server_line:<60}|
|  {api_line:<60}|
|  {docs_line:<60}|
|  {tls_line:<60}|
+--------------------------------------------------------------+
|  Press Ctrl+C for graceful shutdown                          |
|  Data stored in: ~/.plexichat/                               |
+==============================================================+
        """)

        self.server.run()

        self.cleanup()

        return self.restart_requested
