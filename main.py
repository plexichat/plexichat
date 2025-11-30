#!/usr/bin/env python3
"""
PlexiChat Server - Main Entry Point

This script initializes all core modules and starts the API server.
Supports clean shutdown (Ctrl+C) with client notification and restart capability.
"""
import os
import sys
import signal
import asyncio
import threading
from pathlib import Path
from typing import Optional

# Setup paths
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

# Import utilities
import utils.logger as logger
import utils.config as config
import utils.validator as validator
import utils.version as version

# Global Version Definition
VERSION = "a.1.0-1"


class PlexiChatServer:
    """Main server class with lifecycle management."""
    
    def __init__(self):
        self.app = None
        self.server = None
        self.db = None
        self.shutdown_event = threading.Event()
        self.restart_requested = False
        self._modules = {}
    
    def get_default_config(self) -> dict:
        """Get default configuration with home folder data storage."""
        home_dir = Path.home() / ".plexichat"
        
        return {
            "logging": {
                "level": "DEBUG",
                "max_bytes": 10485760,
                "backup_count": 5,
                "zip_logs": True,
                "rotate": True
            },
            "database": {
                "type": "sqlite",
                "path": str(home_dir / "data" / "plexichat.db"),
                "postgres": {
                    "host": "localhost",
                    "port": 5432,
                    "user": "postgres",
                    "password": "",
                    "dbname": "plexichat"
                },
                "connection_pool": {
                    "min_connections": 1,
                    "max_connections": 10
                }
            },
            "authentication": {
                "jwt": {
                    "secret_key": "CHANGE_THIS_IN_PRODUCTION",
                    "algorithm": "HS256",
                    "access_token_expire_minutes": 30,
                    "refresh_token_expire_days": 7
                },
                "password": {
                    "min_length": 8,
                    "require_uppercase": True,
                    "require_lowercase": True,
                    "require_digit": True,
                    "require_special": True
                },
                "account_lockout": {
                    "max_failed_attempts": 5,
                    "lockout_duration_minutes": 15
                },
                "session": {
                    "max_concurrent_sessions": 3
                }
            },
            "encryption": {
                "argon2": {
                    "time_cost": 2,
                    "memory_cost": 65536,
                    "parallelism": 2,
                    "hash_length": 32,
                    "salt_length": 16
                },
                "aes_gcm": {
                    "key_length": 32,
                    "nonce_length": 12,
                    "tag_length": 16
                },
                "snowflake": {
                    "epoch": "2024-01-01T00:00:00Z",
                    "worker_id": 1,
                    "datacenter_id": 1
                }
            },
            "api": {
                "title": "PlexiChat API",
                "version": VERSION,
                "api_prefix": "/api/v1",
                "debug": True,
                "cors_origins": ["*"]
            },
            "server": {
                "host": "0.0.0.0",
                "port": 8000,
                "workers": 1,
                "reload": False
            },
            "application": {
                "name": "PlexiChat",
                "version": VERSION,
                "environment": "development"
            },
            "versioning": {
                "min_supported_version": VERSION,
                "update_url": None
            },
            "storage": {
                "data_dir": str(home_dir / "data"),
                "logs_dir": str(home_dir / "logs"),
                "media_dir": str(home_dir / "media"),
                "temp_dir": str(home_dir / "temp")
            }
        }
    
    def setup_directories(self):
        """Create necessary directories in home folder."""
        home_dir = Path.home() / ".plexichat"
        dirs = ["data", "logs", "media", "temp", "config"]
        
        for d in dirs:
            dir_path = home_dir / d
            dir_path.mkdir(parents=True, exist_ok=True)
        
        logger.debug(f"Created directories in {home_dir}")
    
    def setup_config(self):
        """Setup configuration from file or defaults."""
        # Try project config first, then home folder config
        config_paths = [
            os.path.join(project_root, "config", "config.yaml"),
            Path.home() / ".plexichat" / "config" / "config.yaml"
        ]
        
        config_path = None
        for cp in config_paths:
            if os.path.exists(cp):
                config_path = str(cp)
                break
        
        if not config_path:
            config_path = str(config_paths[0])
        
        config.setup(config_path=config_path, default_config=self.get_default_config())
        return config_path

    def setup_logging(self):
        """Setup logging with configured settings."""
        log_config = config.get("logging", {})
        storage_config = config.get("storage", {})
        
        # Use home folder logs dir or fallback to project logs
        log_dir = storage_config.get("logs_dir", os.path.join(project_root, "logs"))
        
        logger.setup(
            log_dir=log_dir,
            level=log_config.get("level", "INFO"),
            max_bytes=log_config.get("max_bytes", 10485760),
            backup_count=log_config.get("backup_count", 5),
            zip_logs=log_config.get("zip_logs", True),
            rotate=log_config.get("rotate", True)
        )
    
    def setup_utilities(self):
        """Setup validator and version utilities."""
        validator.setup()
        
        versioning_config = config.get("versioning", {})
        version.setup(
            current_version=VERSION,
            min_supported_version=versioning_config.get("min_supported_version", VERSION),
        )
    
    def initialize_modules(self):
        """Initialize all core modules in dependency order."""
        from src.core.database import Database
        from src.core import auth, messaging, servers, relationships, presence, reactions, embeds, webhooks, settings
        
        # Initialize database
        logger.info("Initializing database...")
        self.db = Database()
        self.db.connect()
        
        # Initialize core modules in dependency order
        logger.info("Initializing auth module...")
        auth.setup(self.db)
        self._modules['auth'] = auth
        
        logger.info("Initializing messaging module...")
        messaging.setup(self.db, auth)
        self._modules['messaging'] = messaging
        
        logger.info("Initializing servers module...")
        servers.setup(self.db, auth, messaging)
        self._modules['servers'] = servers
        
        logger.info("Initializing relationships module...")
        relationships.setup(self.db, auth, servers)
        self._modules['relationships'] = relationships
        
        logger.info("Initializing presence module...")
        presence.setup(self.db, auth, relationships, servers)
        self._modules['presence'] = presence
        
        logger.info("Initializing reactions module...")
        reactions.setup(self.db, messaging, servers, relationships)
        self._modules['reactions'] = reactions
        
        logger.info("Initializing embeds module...")
        embeds.setup(self.db, messaging, servers)
        self._modules['embeds'] = embeds
        
        logger.info("Initializing webhooks module...")
        webhooks.setup(self.db, auth, messaging, servers, embeds)
        self._modules['webhooks'] = webhooks
        
        logger.info("Initializing settings module...")
        settings.setup(self.db)
        self._modules['settings'] = settings
        
        return auth, messaging, servers, relationships, presence, reactions, embeds, webhooks, settings
    
    def create_application(self, auth, messaging, servers, relationships, presence, reactions, embeds, webhooks, settings):
        """Create and configure the FastAPI application."""
        from src.api import setup as api_setup, create_app
        
        logger.info("Initializing API module...")
        api_setup(
            db=self.db,
            auth_module=auth,
            messaging_module=messaging,
            servers_module=servers,
            relationships_module=relationships,
            presence_module=presence,
            reactions_module=reactions,
            embeds_module=embeds,
            webhooks_module=webhooks,
            settings_module=settings,
        )
        
        self.app = create_app(enable_rate_limiting=False, enable_docs=True)
        return self.app
    
    async def notify_clients_shutdown(self, message: str = "Server shutting down"):
        """Notify connected WebSocket clients about shutdown."""
        try:
            # Import websocket module if available
            from src.api.websocket import gateway
            if hasattr(gateway, 'broadcast_server_status'):
                await gateway.broadcast_server_status({
                    "state": "shutting_down",
                    "message": message,
                    "restart_at": None
                })
                logger.info("Notified clients of shutdown")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")
    
    async def notify_clients_restart(self, estimated_seconds: int = 10):
        """Notify connected WebSocket clients about restart."""
        try:
            from src.api.websocket import gateway
            if hasattr(gateway, 'broadcast_server_status'):
                await gateway.broadcast_server_status({
                    "state": "restarting",
                    "message": "Server is restarting",
                    "estimated_downtime_seconds": estimated_seconds
                })
                logger.info("Notified clients of restart")
        except Exception as e:
            logger.debug(f"Could not notify clients: {e}")
    
    def cleanup(self):
        """Clean up resources on shutdown."""
        logger.info("Cleaning up resources...")
        
        # Close database connection
        if self.db:
            try:
                self.db.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")
        
        logger.info("Cleanup complete")

    def run(self, host: str = None, port: int = None):
        """Run the server with graceful shutdown support."""
        import uvicorn
        
        server_config = config.get("server", {})
        host = host or server_config.get("host", "0.0.0.0")
        port = port or server_config.get("port", 8000)
        
        # Create uvicorn config
        uvi_config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="info",
            loop="asyncio"
        )
        
        self.server = uvicorn.Server(uvi_config)
        
        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self.shutdown_event.set()
            
            # Run async notification in event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.notify_clients_shutdown())
            except Exception:
                pass
            
            self.server.should_exit = True
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        logger.info("All modules initialized successfully")
        logger.info("=" * 60)
        
        print(f"""
+==============================================================+
|                    PlexiChat API Server                      |
|                      Version {VERSION:<32}|
+==============================================================+
|  Server: http://{host}:{port:<39}|
|  API:    http://{host}:{port}/api/v1{' ' * 26}|
|  Docs:   http://{host}:{port}/docs{' ' * 28}|
+--------------------------------------------------------------+
|  Press Ctrl+C for graceful shutdown                          |
|  Data stored in: ~/.plexichat/                               |
+==============================================================+
        """)
        
        # Run the server
        self.server.run()
        
        # Cleanup after server stops
        self.cleanup()
        
        return self.restart_requested
    
    def request_restart(self):
        """Request a server restart."""
        self.restart_requested = True
        self.shutdown_event.set()
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.notify_clients_restart())
        except Exception:
            pass
        
        if self.server:
            self.server.should_exit = True


def main():
    """Main entry point for the PlexiChat server."""
    server = PlexiChatServer()
    
    # Buffer for early log messages (before logger is configured)
    early_logs = []
    
    # Setup directories in home folder first (buffer logs)
    home_dir = Path.home() / ".plexichat"
    dirs = ["data", "logs", "media", "temp", "config"]
    for d in dirs:
        dir_path = home_dir / d
        dir_path.mkdir(parents=True, exist_ok=True)
        early_logs.append(("debug", f"Ensured directory exists: {dir_path}"))
    
    # Setup configuration (needed for logger config)
    config_path = server.setup_config()
    early_logs.append(("debug", f"Config loaded from: {config_path}"))
    
    # Setup logging - NOW we can use logger
    server.setup_logging()
    
    # Replay buffered logs
    for level, msg in early_logs:
        getattr(logger, level)(msg)
    
    # Now we can log normally
    logger.info("=" * 60)
    logger.info("PlexiChat Server Starting")
    logger.info("=" * 60)
    
    # Log startup info
    app_config = config.get("application", {})
    logger.info(f"Application: {app_config.get('name', 'PlexiChat')}")
    logger.info(f"Version: {VERSION}")
    logger.info(f"Environment: {app_config.get('environment', 'development')}")
    logger.info(f"Config file: {config_path}")
    
    # Setup utilities
    server.setup_utilities()
    
    # Initialize modules
    modules = server.initialize_modules()
    
    # Create application
    server.create_application(*modules)
    
    # Get server settings
    host = os.getenv("HOST", config.get("server", {}).get("host", "0.0.0.0"))
    port = int(os.getenv("PORT", config.get("server", {}).get("port", 8000)))
    
    # Run server (returns True if restart was requested)
    should_restart = server.run(host, port)
    
    if should_restart:
        logger.info("Restart requested, restarting server...")
        # Re-execute the script
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    logger.info("Server shutdown complete")


if __name__ == "__main__":
    main()
