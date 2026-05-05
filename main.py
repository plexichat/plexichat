#!/usr/bin/env python3
"""
Plexichat Server - Main Entry Point

This script initializes all core modules and starts the API server.
Supports clean shutdown (Ctrl+C) with client notification and restart capability.
"""

# pyright: reportAttributeAccessIssue=false
# Database class uses mixins which pyright's protocol checking doesn't fully recognize
import os
import sys
import signal
import asyncio
import threading
import argparse
import secrets
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.database import Database

# Setup paths
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

# Import utilities
import utils.logger as logger  # noqa: E402
import utils.config as config  # noqa: E402
import utils.validator as validator  # noqa: E402

validator.setup(auto_sanitize_html=True)
import utils.version as version  # noqa: E402

# Global Version Definition
VERSION = "a.1.0-53"


class PlexichatServer:
    """Main server class with lifecycle management."""

    def __init__(self):
        self.app = None
        self.server = None
        self.db: Optional["Database"] = None
        self.shutdown_event = threading.Event()
        self.restart_requested = False
        self._modules = {}
        # Generate a unique worker ID for this process instance
        self.worker_id = secrets.token_hex(4)

    def get_default_config(self) -> Dict[str, Any]:
        """Get default configuration from external defaults module."""
        from src.config_defaults import get_default_config as fetch_default_config

        return fetch_default_config(version=VERSION)

    def setup_config(self) -> str:
        """Setup configuration from file or defaults, with environment variable overrides.

        Config file search order:
        1. Project config: ./config/config.yaml
        2. Home folder config: ~/.plexichat/config/config.yaml
        """
        # Try project config first, then home folder config
        config_paths = [
            (os.path.join(project_root, "config", "config.yaml"), "project directory"),
            (
                str(Path.home() / ".plexichat" / "config" / "config.yaml"),
                "home directory",
            ),
        ]

        config_path = None
        for cp, source in config_paths:
            if os.path.exists(cp):
                config_path = str(cp)
                break

        if not config_path:
            # Use project config as default (will be created)
            config_path = str(config_paths[0][0])

        config.setup(config_path=config_path, default_config=self.get_default_config())

        # Apply environment variable overrides
        self._apply_env_overrides()

        return config_path

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides to configuration.

        Supports the following environment variables:

        DATABASE CONFIGURATION:
        - DATABASE_URL: Full PostgreSQL or SQLite connection string
        - POSTGRES_HOST: PostgreSQL host (default: localhost)
        - POSTGRES_PORT: PostgreSQL port (default: 5432)
        - POSTGRES_USER: PostgreSQL username (default: postgres)
        - POSTGRES_PASSWORD: PostgreSQL password (required for production)
        - POSTGRES_DBNAME: PostgreSQL database name (default: plexichat)
        - POSTGRES_SSLMODE: PostgreSQL SSL mode (default: prefer)

        DATABASE CONNECTION POOL:
        - DB_POOL_MIN_CONNECTIONS: Minimum connections (default: 1)
        - DB_POOL_MAX_CONNECTIONS: Maximum connections (default: 10)
        - DB_POOL_CONNECT_TIMEOUT: Connection timeout in seconds (default: 10)
        - DB_POOL_MAX_IDLE_TIME: Idle timeout in seconds (default: 300)
        - DB_POOL_VALIDATION_INTERVAL: Validation check interval in seconds (default: 60)
        - DB_POOL_ENABLE_VALIDATION: Enable connection validation (default: true)
        - DB_POOL_VALIDATION_QUERY: Query for validation (default: SELECT 1)

        MONITORING:
        - MONITORING_ENABLED: Enable monitoring (default: true)
        - MONITORING_LOG_INTERVAL: Metrics log interval in seconds (default: 300)
        - MONITORING_METRICS_ENABLED: Enable metrics collection (default: true)
        - MONITORING_ALERT_CPU_THRESHOLD: CPU alert threshold % (default: 80)
        - MONITORING_ALERT_MEMORY_THRESHOLD: Memory alert threshold % (default: 85)
        - MONITORING_ALERT_DB_POOL_THRESHOLD: DB pool saturation % (default: 75)
        - MONITORING_ALERT_QUERY_TIME_MS: Query timeout alert in ms (default: 5000)
        - MONITORING_ALERT_DB_ERRORS_PER_MINUTE: DB error rate alert (default: 10)
        - MONITORING_ALERT_API_RESPONSE_TIME_MS: API response time alert (default: 2000)
        - MONITORING_ALERT_ERROR_RATE_PERCENT: Error rate alert % (default: 5)
        - MONITORING_ALERT_ACTIVE_CONNECTIONS: Connection count alert (default: 1000)

        LOGGING:
        - LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        import urllib.parse

        db_config = config.get("database", {})

        # Handle DATABASE_URL (takes precedence over individual env vars)
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            if database_url.startswith("postgres://") or database_url.startswith(
                "postgresql://"
            ):
                # Parse PostgreSQL URL
                parsed = urllib.parse.urlparse(database_url)
                db_config["type"] = "postgres"
                db_config["postgres"] = {
                    "host": parsed.hostname or "localhost",
                    "port": parsed.port or 5432,
                    "user": parsed.username or "postgres",
                    "password": parsed.password or "",
                    "dbname": parsed.path.lstrip("/") if parsed.path else "plexichat",
                    "sslmode": "prefer",
                }
                # Parse query params for sslmode
                if parsed.query:
                    params = urllib.parse.parse_qs(parsed.query)
                    if "sslmode" in params:
                        db_config["postgres"]["sslmode"] = params["sslmode"][0]
            elif database_url.startswith("sqlite:///"):
                db_config["type"] = "sqlite"
                db_config["path"] = database_url[10:]  # Remove sqlite:///
        else:
            # Handle individual PostgreSQL environment variables
            if not db_config.get("postgres"):
                db_config["postgres"] = {}

            postgres_config = db_config["postgres"]

            # PostgreSQL connection credentials
            postgres_host = os.getenv("POSTGRES_HOST")
            if postgres_host:
                postgres_config["host"] = postgres_host

            postgres_port = os.getenv("POSTGRES_PORT")
            if postgres_port:
                try:
                    postgres_config["port"] = int(postgres_port)
                except ValueError:
                    logger.warning(
                        f"Invalid POSTGRES_PORT value: {postgres_port}, using default"
                    )

            postgres_user = os.getenv("POSTGRES_USER")
            if postgres_user:
                postgres_config["user"] = postgres_user

            postgres_password = os.getenv("POSTGRES_PASSWORD")
            if postgres_password:
                postgres_config["password"] = postgres_password

            postgres_dbname = os.getenv("POSTGRES_DBNAME")
            if postgres_dbname:
                postgres_config["dbname"] = postgres_dbname

            postgres_sslmode = os.getenv("POSTGRES_SSLMODE")
            if postgres_sslmode:
                postgres_config["sslmode"] = postgres_sslmode

        # Initialize connection pool config if not present
        if "connection_pool" not in db_config:
            db_config["connection_pool"] = {}

        pool_config = db_config["connection_pool"]

        # Connection pool environment variables
        db_pool_min = os.getenv("DB_POOL_MIN_CONNECTIONS")
        if db_pool_min:
            try:
                pool_config["min_connections"] = int(db_pool_min)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_MIN_CONNECTIONS value: {db_pool_min}")

        db_pool_max = os.getenv("DB_POOL_MAX_CONNECTIONS")
        if db_pool_max:
            try:
                pool_config["max_connections"] = int(db_pool_max)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_MAX_CONNECTIONS value: {db_pool_max}")

        db_pool_timeout = os.getenv("DB_POOL_CONNECT_TIMEOUT")
        if db_pool_timeout:
            try:
                pool_config["connect_timeout"] = int(db_pool_timeout)
            except ValueError:
                logger.warning(
                    f"Invalid DB_POOL_CONNECT_TIMEOUT value: {db_pool_timeout}"
                )

        db_pool_idle = os.getenv("DB_POOL_MAX_IDLE_TIME")
        if db_pool_idle:
            try:
                pool_config["max_idle_time"] = int(db_pool_idle)
            except ValueError:
                logger.warning(f"Invalid DB_POOL_MAX_IDLE_TIME value: {db_pool_idle}")

        db_pool_validation_interval = os.getenv("DB_POOL_VALIDATION_INTERVAL")
        if db_pool_validation_interval:
            try:
                pool_config["validation_interval"] = int(db_pool_validation_interval)
            except ValueError:
                logger.warning(
                    f"Invalid DB_POOL_VALIDATION_INTERVAL value: {db_pool_validation_interval}"
                )

        db_pool_enable_validation = os.getenv("DB_POOL_ENABLE_VALIDATION")
        if db_pool_enable_validation:
            pool_config["enable_validation"] = db_pool_enable_validation.lower() in (
                "true",
                "1",
                "yes",
            )

        db_pool_validation_query = os.getenv("DB_POOL_VALIDATION_QUERY")
        if db_pool_validation_query:
            # SECURITY: Strictly validate the validation query to prevent SQL injection via config
            allowed_queries = ["SELECT 1", "SELECT 1;", "SELECT version();"]
            if db_pool_validation_query.strip().upper() in [
                q.upper() for q in allowed_queries
            ]:
                pool_config["validation_query"] = db_pool_validation_query
            else:
                logger.warning(
                    f"Blocked potentially unsafe DB_POOL_VALIDATION_QUERY: {db_pool_validation_query}"
                )
                pool_config["validation_query"] = "SELECT 1"

        config.set("database", db_config)

        # Monitoring configuration environment variables
        monitoring_config = config.get("monitoring", {})

        monitoring_enabled = os.getenv("MONITORING_ENABLED")
        if monitoring_enabled:
            monitoring_config["enabled"] = monitoring_enabled.lower() in (
                "true",
                "1",
                "yes",
            )

        monitoring_log_interval = os.getenv("MONITORING_LOG_INTERVAL")
        if monitoring_log_interval:
            try:
                monitoring_config["log_interval"] = int(monitoring_log_interval)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_LOG_INTERVAL value: {monitoring_log_interval}"
                )

        monitoring_metrics = os.getenv("MONITORING_METRICS_ENABLED")
        if monitoring_metrics:
            monitoring_config["metrics_enabled"] = monitoring_metrics.lower() in (
                "true",
                "1",
                "yes",
            )

        # Initialize alert thresholds if not present
        if "alert_thresholds" not in monitoring_config:
            monitoring_config["alert_thresholds"] = {}

        alert_thresholds = monitoring_config["alert_thresholds"]

        # Individual alert threshold environment variables
        cpu_threshold = os.getenv("MONITORING_ALERT_CPU_THRESHOLD")
        if cpu_threshold:
            try:
                alert_thresholds["cpu_percent"] = float(cpu_threshold)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_CPU_THRESHOLD value: {cpu_threshold}"
                )

        memory_threshold = os.getenv("MONITORING_ALERT_MEMORY_THRESHOLD")
        if memory_threshold:
            try:
                alert_thresholds["memory_percent"] = float(memory_threshold)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_MEMORY_THRESHOLD value: {memory_threshold}"
                )

        db_pool_threshold = os.getenv("MONITORING_ALERT_DB_POOL_THRESHOLD")
        if db_pool_threshold:
            try:
                alert_thresholds["db_pool_saturation_percent"] = float(
                    db_pool_threshold
                )
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_DB_POOL_THRESHOLD value: {db_pool_threshold}"
                )

        query_time = os.getenv("MONITORING_ALERT_QUERY_TIME_MS")
        if query_time:
            try:
                alert_thresholds["query_time_ms"] = int(query_time)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_QUERY_TIME_MS value: {query_time}"
                )

        db_errors = os.getenv("MONITORING_ALERT_DB_ERRORS_PER_MINUTE")
        if db_errors:
            try:
                alert_thresholds["db_errors_per_minute"] = int(db_errors)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_DB_ERRORS_PER_MINUTE value: {db_errors}"
                )

        api_response_time = os.getenv("MONITORING_ALERT_API_RESPONSE_TIME_MS")
        if api_response_time:
            try:
                alert_thresholds["api_response_time_ms"] = int(api_response_time)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_API_RESPONSE_TIME_MS value: {api_response_time}"
                )

        error_rate = os.getenv("MONITORING_ALERT_ERROR_RATE_PERCENT")
        if error_rate:
            try:
                alert_thresholds["error_rate_percent"] = float(error_rate)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_ERROR_RATE_PERCENT value: {error_rate}"
                )

        active_connections = os.getenv("MONITORING_ALERT_ACTIVE_CONNECTIONS")
        if active_connections:
            try:
                alert_thresholds["active_connections"] = int(active_connections)
            except ValueError:
                logger.warning(
                    f"Invalid MONITORING_ALERT_ACTIVE_CONNECTIONS value: {active_connections}"
                )

        config.set("monitoring", monitoring_config)

        # LOG_LEVEL override
        log_level = os.getenv("LOG_LEVEL")
        if log_level:
            log_config = config.get("logging", {})
            log_config["level"] = log_level.upper()
            config.set("logging", log_config)

    def validate_config(self) -> None:
        """Validate current configuration against required keys and types."""
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
            # For some critical keys we might want to raise an error,
            # but for now we'll just log warnings as defaults are applied by the config util.

        if type_mismatches:
            error_msg = f"Configuration type mismatches: {', '.join(type_mismatches)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def setup_logging(self) -> None:
        """Setup logging with configured settings."""
        log_config = config.get("logging", {})
        media_config = config.get("media", {})

        # Use home folder logs dir or fallback to project logs
        log_dir = media_config.get("logs_dir", os.path.join(project_root, "logs"))

        # Expand ~ to home directory
        log_dir = os.path.expanduser(log_dir)

        logger.setup(
            log_dir=log_dir,
            level=log_config.get("level", "INFO"),
            max_bytes=log_config.get("max_bytes", 10485760),
            backup_count=log_config.get("backup_count", 5),
            zip_logs=log_config.get("zip_logs", True),
            rotate=log_config.get("rotate", True),
        )

    def setup_utilities(self) -> None:
        """Setup validator, version and encryption utilities."""
        validator.setup()

        versioning_config = config.get("versioning", {})
        version.setup(
            current_version=VERSION,
            min_supported_version=versioning_config.get(
                "min_supported_version", VERSION
            ),
        )

        # Initialize encryption with config values
        from src.utils import encryption

        encryption_config = config.get("encryption", {})

        # SECURITY: Check for secure key source if configured
        auth_config = config.get("authentication", {})
        enc_security = auth_config.get("encryption", {})
        if enc_security.get("require_secure_source", False):
            from src.utils.encryption.vault import vault

            if not vault.is_using_secure_source():
                error_msg = (
                    "CRITICAL SECURITY ERROR: Application is configured to require a secure "
                    "encryption key source (TPM or Environment Variable), but none was found. "
                    "The application has fallen back to an insecure local key file. "
                    "To fix: Set PLEXICHAT_SYSTEM_KEY env var or ensure TPM is accessible. "
                    "To bypass (DEV ONLY): Set authentication.encryption.require_secure_source to False."
                )
                logger.critical(error_msg)
                raise RuntimeError(error_msg)

        encryption.setup(
            worker_id=encryption_config.get("snowflake", {}).get("worker_id", 1) or 1,
            datacenter_id=encryption_config.get("snowflake", {}).get("datacenter_id", 1)
            or 1,
            argon2_time_cost=encryption_config.get("argon2", {}).get("time_cost", 2),
            argon2_memory_cost=encryption_config.get("argon2", {}).get(
                "memory_cost", 65536
            ),
            argon2_parallelism=encryption_config.get("argon2", {}).get(
                "parallelism", 2
            ),
        )

        # Eagerly validate all keyrings at startup so the server fails fast
        # if any keyring file is corrupted or the KEK has changed.
        # system_keyring is already validated by EncryptionManager.__init__
        # (called inside encryption.setup()), but file_keyring and
        # message_keyring are loaded lazily — so we validate them now.
        from src.utils.encryption.core import Keyring

        keyring_paths = [
            (Path.home() / ".plexichat" / "data" / "file_keyring.json", None),
            (
                Path.home() / ".plexichat" / "data" / "message_keyring.json",
                "PLEXICHAT_MESSAGE_KEY",
            ),
        ]
        for kpath, kek_env_var in keyring_paths:
            if kpath.exists():
                if kek_env_var:
                    kr = Keyring(kpath, kek_env_var)  # type: ignore[arg-type]
                else:
                    kr = Keyring(kpath)
                if kr.keys:
                    logger.info(
                        f"Keyring validated: {kpath.name} (v{kr.current_version})"
                    )

    def initialize_modules(
        self,
    ) -> Tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any]:
        """Initialize all core modules in dependency order."""
        from src.core.database import Database, setup_redis
        from src.core import (
            auth,
            messaging,
            polls,
            servers,
            relationships,
            presence,
            reactions,
            stickers,
            embeds,
            webhooks,
            settings,
            media,
            events,
            automod,
            search,
        )
        from src.core import voice, notifications, threads
        from src.core.voice import signaling
        import src.api as api

        # Generate and set internal secret for secure self-test validation
        internal_secret = secrets.token_hex(32)
        api.set_internal_secret(internal_secret)
        logger.info("Internal security secret generated for self-test")

        # Persist long-lived secrets if they don't exist
        app_config = config.get("applications", {})
        if not app_config.get("webhook_signature_secret"):
            app_config["webhook_signature_secret"] = secrets.token_hex(32)
            config.set("applications", app_config)
            logger.info("Generated and persisted webhook_signature_secret")

        rl_config = config.get("rate_limiting", {})
        if not rl_config.get("bypass_secret"):
            rl_config["bypass_secret"] = secrets.token_hex(32)
            config.set("rate_limiting", rl_config)
            logger.info("Generated and persisted rate-limit bypass_secret")

        failed_modules = []

        # Initialize database
        logger.info("Initializing database...")
        self.db = Database()

        # Run migrations
        from src.core.migrations import run_migrations

        try:
            migration_result = run_migrations(self.db)
            if migration_result["success"]:
                logger.info(
                    f"Migrations applied successfully: "
                    f"{migration_result['applied_count']} applied, "
                    f"{migration_result['failed_count']} failed"
                )
            else:
                logger.error(
                    f"Migration process had failures: "
                    f"{migration_result['applied_count']} applied, "
                    f"{migration_result['failed_count']} failed"
                )
        except Exception as e:
            logger.error(f"Critical error during migrations: {e}")
            raise

        self.db.connect()

        # Initialize Redis if enabled
        redis_config = config.get("redis") or {}
        if redis_config.get("enabled", False):
            logger.info("Initializing Redis...")
            redis_client = setup_redis()
            if redis_client and redis_client.ping():
                # Register our unique worker ID
                redis_client.set_worker_id(self.worker_id)
                logger.info(
                    f"Connected to Redis at {redis_config.get('host', 'localhost')}:{redis_config.get('port', 6379)} (Worker ID: {self.worker_id})"
                )
            else:
                logger.warning(
                    "Redis is enabled but connection failed - continuing without Redis"
                )
        else:
            logger.info("Redis is disabled in configuration")

        # Check encryption key rotation
        try:
            from src.utils import encryption

            if encryption.rotate_keys():
                logger.info("Encryption keys rotated successfully")
        except Exception as e:
            logger.error(f"Failed to check encryption key rotation: {e}")

        # Initialize core modules in parallel where possible
        startup_times = {}
        startup_lock = threading.Lock()

        def timed_init(name: str, init_func):
            """Initialize a module and track how long it takes."""
            start = time.perf_counter()
            logger.info(f"Initializing {name} module...")
            try:
                result = init_func()
                elapsed = (time.perf_counter() - start) * 1000
                with startup_lock:
                    startup_times[name] = elapsed
                logger.info(f"  -> {name} initialized in {elapsed:.1f}ms")
                return result
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                with startup_lock:
                    startup_times[name] = elapsed
                logger.error(f"  -> {name} FAILED after {elapsed:.1f}ms: {e}")
                raise

        # Independent initialization group (Parallel)
        def init_independent():
            threads = []

            # 1. Servers (Heavy DB)
            def init_servers():
                timed_init("servers", lambda: servers.setup(self.db, auth, messaging))
                self._modules["servers"] = servers

            threads.append(threading.Thread(target=init_servers, name="InitServers"))

            # 2. Relationships
            def init_rel():
                timed_init(
                    "relationships", lambda: relationships.setup(self.db, auth, servers)
                )
                self._modules["relationships"] = relationships

            threads.append(threading.Thread(target=init_rel, name="InitRel"))

            # 3. Media
            def init_media():
                timed_init("media", lambda: media.setup(self.db, messaging))
                self._modules["media"] = media

            threads.append(threading.Thread(target=init_media, name="InitMedia"))

            # 4. Settings
            def init_settings():
                timed_init("settings", lambda: settings.setup(self.db))
                self._modules["settings"] = settings

            threads.append(threading.Thread(target=init_settings, name="InitSettings"))

            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Initialize email sender if configured
        email_config = config.get("email", {})
        email_sender = None
        smtp_password = os.getenv("PLEXICHAT_SMTP_PASSWORD")
        if email_config.get("smtp_host") and smtp_password:
            from src.utils.email import SMTPEmailSender

            email_sender = SMTPEmailSender(
                host=email_config["smtp_host"],
                port=email_config.get("smtp_port", 587),
                user=email_config.get("smtp_user", ""),
                password=smtp_password,
                from_email=email_config.get("from_email", "noreply@plexichat.internal"),
                use_tls=email_config.get("use_tls", True),
            )
            logger.info(
                f"Email sender initialized via SMTP ({email_config['smtp_host']})"
            )
        else:
            logger.info(
                "Email sender not initialized (SMTP host or PLEXICHAT_SMTP_PASSWORD missing)"
            )

        # Core initialization group (Serial due to deep dependencies)
        timed_init("auth", lambda: auth.setup(self.db, email_sender=email_sender))
        self._modules["auth"] = auth

        timed_init("messaging", lambda: messaging.setup(self.db, auth))
        self._modules["messaging"] = messaging

        timed_init("search", lambda: search.setup(self.db, auth, messaging, servers))
        self._modules["search"] = search

        init_independent()

        # Dependent initialization group (Parallel)
        def init_dependent():
            threads = []

            # Start Account Reaper (handles boot-check and background purge)
            try:
                from src.core.auth.reaper import AccountReaper

                reaper = AccountReaper(self.db, auth)
                reaper.start()
                self._modules["reaper"] = reaper
            except Exception as e:
                logger.error(f"Failed to start Account Reaper: {e}")

            # 1. Presence
            def init_presence():
                timed_init(
                    "presence",
                    lambda: presence.setup(self.db, auth, relationships, servers),
                )
                self._modules["presence"] = presence

            threads.append(threading.Thread(target=init_presence, name="InitPresence"))

            # 2. Reactions
            def init_reactions():
                timed_init(
                    "reactions",
                    lambda: reactions.setup(self.db, messaging, servers, relationships),
                )
                self._modules["reactions"] = reactions

            threads.append(
                threading.Thread(target=init_reactions, name="InitReactions")
            )

            # 3. Embeds
            def init_embeds():
                timed_init("embeds", lambda: embeds.setup(self.db, messaging, servers))
                self._modules["embeds"] = embeds

            threads.append(threading.Thread(target=init_embeds, name="InitEmbeds"))

            # 4. Polls
            def init_polls():
                timed_init("polls", lambda: polls.setup(self.db, messaging))
                self._modules["polls"] = polls

            threads.append(threading.Thread(target=init_polls, name="InitPolls"))

            # 5. Notifications (Complex)
            def init_notifications():
                try:
                    timed_init(
                        "notifications",
                        lambda: notifications.setup(
                            self.db,
                            auth_module=auth,
                            messaging_module=messaging,
                            servers_module=servers,
                            relationships_module=relationships,
                            presence_module=presence,
                        ),
                    )
                    self._modules["notifications"] = notifications
                except Exception as e:
                    logger.warning(f"Failed to initialize notifications module: {e}")

            threads.append(
                threading.Thread(target=init_notifications, name="InitNotifications")
            )

            # 6. Stickers
            def init_stickers():
                timed_init(
                    "stickers",
                    lambda: stickers.setup(
                        self.db,
                        messaging_module=messaging,
                        servers_module=servers,
                        media_module=media,
                    ),
                )
                self._modules["stickers"] = stickers

            threads.append(threading.Thread(target=init_stickers, name="InitStickers"))

            for t in threads:
                t.start()
            for t in threads:
                t.join()

        init_dependent()

        # Initialize AutoMod
        timed_init(
            "automod",
            lambda: automod.setup(
                self.db,
                servers_module=servers,
                messaging_module=messaging,
                notifications_module=self._modules.get("notifications"),
            ),
        )
        self._modules["automod"] = automod

        # Ensure default rules for all servers
        try:
            all_servers = self.db.fetch_all(
                "SELECT id, owner_id FROM srv_servers WHERE deleted = 0"
            )
            for srv in all_servers:
                automod.ensure_default_rules(srv["id"], srv["owner_id"])
            if all_servers:
                logger.info(
                    f"Ensured default automod rules for {len(all_servers)} servers"
                )
        except Exception as e:
            logger.warning(f"Failed to ensure default rules for servers: {e}")

        # Final non-critical group
        timed_init(
            "events",
            lambda: events.setup(
                relationships_module=relationships,
                servers_module=servers,
                messaging_module=messaging,
            ),
        )
        self._modules["events"] = events

        timed_init(
            "webhooks",
            lambda: webhooks.setup(self.db, auth, messaging, servers, embeds),
        )
        self._modules["webhooks"] = webhooks

        try:
            timed_init("threads", lambda: threads.setup(self.db, messaging, servers))
            self._modules["threads"] = threads
        except Exception as e:
            logger.warning(f"Failed to initialize threads module: {e}")
            failed_modules.append("threads")

        # Initialize user features module
        features = None
        try:
            from src.core import features

            timed_init("features", lambda: features.setup(self.db))
            self._modules["features"] = features
        except Exception as e:
            logger.warning(f"Failed to initialize user features module: {e}")
            failed_modules.append("features")

        # Initialize avatars module
        try:
            from src.core import avatars

            timed_init("avatars", lambda: avatars.setup(self.db))
            self._modules["avatars"] = avatars
        except Exception as e:
            logger.warning(f"Failed to initialize avatars module: {e}")
            failed_modules.append("avatars")

        # Initialize voice module
        try:
            timed_init(
                "voice",
                lambda: voice.setup(self.db, auth, servers, relationships, presence),
            )
            self._modules["voice"] = voice
        except Exception as e:
            logger.warning(f"Failed to initialize voice module: {e}")
            failed_modules.append("voice")

        # Initialize voice signaling with SFU configuration
        voice_config = config.get("voice") or {}
        if voice_config.get("enabled", False):
            try:
                sfu_backend = voice_config.get("sfu_backend", "aiortc")

                def init_signaling():
                    signaling.setup(
                        voice_module=voice,
                        events_module=events,
                        sfu_backend=sfu_backend,
                        mediasoup_url=voice_config.get(
                            "mediasoup_url", "https://localhost:4443"
                        ),
                        mediasoup_origin=voice_config.get(
                            "mediasoup_origin",
                            "https://localhost:4443",
                        ),
                        janus_url=voice_config.get(
                            "janus_url", "http://localhost:8088/janus"
                        ),
                        stun_urls=voice_config.get(
                            "stun_urls", ["stun:stun.l.google.com:19302"]
                        ),
                        turn_urls=voice_config.get("turn_urls", []),
                        turn_secret=voice_config.get("turn_secret", ""),
                        turn_ttl=voice_config.get("turn_ttl", 86400),
                        turn_username=voice_config.get("turn_username", ""),
                        turn_credential=voice_config.get("turn_credential", ""),
                    )

                timed_init("signaling", init_signaling)
                self._modules["signaling"] = signaling
                sfu_url = (
                    voice_config.get("mediasoup_url")
                    if sfu_backend in ("mediasoup", "mediasoup-ws")
                    else voice_config.get("janus_url")
                )
                logger.info(
                    f"Voice signaling initialized with {sfu_backend} backend at {sfu_url}"
                )
                if voice_config.get("turn_urls"):
                    logger.info(
                        f"TURN servers configured: {len(voice_config.get('turn_urls', []))} servers"
                    )
                if voice_config.get("log_connections", False):
                    logger.info("Voice connection logging enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize voice signaling module: {e}")
                failed_modules.append("signaling")
        else:
            logger.info("Voice module disabled in configuration")

        # Initialize rate limit module
        rate_limit_settings = config.get("rate_limiting", {})
        if rate_limit_settings.get("enabled", True):
            try:
                from src.core import ratelimit
                from src.core.ratelimit.storage import (
                    RedisStorage,
                    MemoryStorage,
                    DatabaseStorage,
                )
                from src.core.database.redis_client import (
                    is_available as redis_is_available,
                )

                def init_ratelimit():
                    storage = None
                    if redis_is_available():
                        storage = RedisStorage()
                        logger.info("Using Redis storage for rate limiting")
                    elif self.db:
                        storage = DatabaseStorage(self.db)
                        db_type = self.db.type.capitalize()
                        logger.info(
                            f"Using {db_type} storage for rate limiting (shared across workers)"
                        )
                    else:
                        storage = MemoryStorage()
                        logger.info(
                            "Using in-memory storage for rate limiting (fallback)"
                        )

                    ratelimit.setup(
                        storage_backend=storage,
                        bot_multiplier=rate_limit_settings.get("bot_multiplier", 1.5),
                        webhook_multiplier=rate_limit_settings.get(
                            "webhook_multiplier", 1.0
                        ),
                        enable_global_limit=True,
                    )

                timed_init("ratelimit", init_ratelimit)
                self._modules["ratelimit"] = ratelimit
            except Exception as e:
                logger.warning(f"Failed to initialize rate limit module: {e}")
                failed_modules.append("ratelimit")

        # Initialize telemetry module
        telemetry_config = config.get("telemetry") or {}
        if telemetry_config.get("enabled", True):
            try:
                from src.core import telemetry

                timed_init("telemetry", lambda: telemetry.setup(self.db))
                self._modules["telemetry"] = telemetry
            except Exception as e:
                logger.warning(f"Failed to initialize telemetry module: {e}")
                failed_modules.append("telemetry")

        # Initialize feedback module
        feedback_config = config.get("feedback") or {}
        if feedback_config.get("enabled", True):
            try:
                from src.core import feedback

                timed_init("feedback", lambda: feedback.setup(self.db))
                self._modules["feedback"] = feedback
            except Exception as e:
                logger.warning(f"Failed to initialize feedback module: {e}")
                failed_modules.append("feedback")

        # Initialize reports module
        reports_config = config.get("reports") or {}
        if reports_config.get("enabled", True):
            try:
                from src.core import reports

                timed_init("reports", lambda: reports.setup(self.db, messaging))
                self._modules["reports"] = reports
            except Exception as e:
                logger.warning(f"Failed to initialize reports module: {e}")
                failed_modules.append("reports")

        # Initialize admin module
        admin_config = config.get("admin_ui") or {}
        if admin_config.get("enabled", True):
            try:
                from src.core import admin

                timed_init("admin", lambda: admin.setup(self.db, auth, features))
                self._modules["admin"] = admin
                logger.info(
                    f"Admin module initialized (path: {admin_config.get('path', '/admin')})"
                )
            except Exception as e:
                logger.warning(f"Failed to initialize admin module: {e}")
                failed_modules.append("admin")

        # Log summary of initialization with timing
        total_time = sum(startup_times.values())
        logger.info("=" * 60)
        logger.info("MODULE INITIALIZATION SUMMARY")
        logger.info("=" * 60)
        for module_name, elapsed in sorted(startup_times.items(), key=lambda x: -x[1]):
            logger.info(f"  {module_name:20s} {elapsed:8.1f}ms")
        logger.info("-" * 60)
        logger.info(f"  {'TOTAL':20s} {total_time:8.1f}ms")
        logger.info("=" * 60)

        if failed_modules:
            logger.error(
                f"Module initialization incomplete. Failed modules: {', '.join(failed_modules)}"
            )
        else:
            logger.info("All modules initialized successfully")

        return (
            auth,
            messaging,
            servers,
            relationships,
            presence,
            reactions,
            embeds,
            webhooks,
            settings,
            media,
            search,
        )

    def create_application(
        self,
        auth: Any,
        messaging: Any,
        servers: Any,
        relationships: Any,
        presence: Any,
        reactions: Any,
        embeds: Any,
        webhooks: Any,
        settings: Any,
        media: Any,
        search: Any,
    ) -> Any:
        """Create and configure the FastAPI application."""
        from src.api import setup as api_setup, create_app

        start = time.perf_counter()
        logger.info("Initializing API module...")
        api_setup(
            db=self.db,
            auth_module=auth,
            messaging_module=messaging,
            polls_module=self._modules.get("polls"),
            servers_module=servers,
            relationships_module=relationships,
            presence_module=presence,
            reactions_module=reactions,
            stickers_module=self._modules.get("stickers"),
            embeds_module=embeds,
            webhooks_module=webhooks,
            settings_module=settings,
            media_module=media,
            search_module=search,
            features_module=self._modules.get("features"),
            avatars_module=self._modules.get("avatars"),
            reports_module=self._modules.get("reports"),
            feedback_module=self._modules.get("feedback"),
            admin_module=self._modules.get("admin"),
            events_module=self._modules.get("events"),
            notifications_module=self._modules.get("notifications"),
            threads_module=self._modules.get("threads"),
            telemetry_module=self._modules.get("telemetry"),
        )

        # Get rate limiting and docs settings from config
        rate_limit_config = config.get("rate_limiting", {})
        docs_config = config.get("docs", {})
        enable_rate_limiting = rate_limit_config.get("enabled", True)
        enable_docs = docs_config.get("enabled", True)

        self.app = create_app(
            enable_rate_limiting=enable_rate_limiting, enable_docs=enable_docs
        )
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(f"  -> API initialized in {elapsed:.1f}ms")
        return self.app

    async def notify_clients_shutdown(self, message: str = "Server shutting down"):
        """Notify connected WebSocket clients about shutdown."""
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
        """Notify connected WebSocket clients about restart."""
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
        """Clean up resources on shutdown."""
        logger.info("Cleaning up resources...")

        # Notify clients if not already done (this is a backup)
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

        # Clean up WebSocket global state in Redis
        try:
            from src.api import websocket

            if websocket.is_setup():
                manager = websocket.get_session_manager()
                manager.clear_all_global_sessions()
        except Exception as e:
            logger.debug(f"Error cleaning up global sessions: {e}")

        # Close database connection
        if self.db:
            try:
                self.db.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")

        logger.info("Cleanup complete")

    def run(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        """Run the server with graceful shutdown support."""
        import uvicorn

        server_config = config.get("server", {})
        host = host or server_config.get("host", "127.0.0.1")
        port = port or server_config.get("port", 8000)

        # Check TLS configuration
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

        # Create uvicorn config
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

        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            # Map signal numbers to names
            signal_map = {
                signal.SIGINT: "SIGINT",
            }
            if hasattr(signal, "SIGTERM"):
                signal_map[signal.SIGTERM] = "SIGTERM"

            signal_name = signal_map.get(signum, f"signal {signum}")

            # If we're already shutting down, force exit on second signal
            if self.shutdown_event.is_set():
                logger.warning(
                    f"Received second {signal_name}, forcing immediate exit..."
                )
                os._exit(1)

            logger.info(f"Received {signal_name}, initiating graceful shutdown...")
            self.shutdown_event.set()

            # Run async notification in event loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Schedule the notification coroutine
                asyncio.run_coroutine_threadsafe(self.notify_clients_shutdown(), loop)

            if self.server:
                # Tell uvicorn to exit
                self.server.should_exit = True
                # Also set force_exit to False to allow graceful shutdown first
                # self.server.force_exit = False

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)

        # SIGTERM is not available on Windows, only register if supported
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, signal_handler)

        logger.info("All modules initialized successfully")
        logger.info("=" * 60)

        # Get version string from version utility
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

        # Run the server
        self.server.run()

        # Cleanup after server stops
        self.cleanup()

        return self.restart_requested


def main() -> None:
    """Main entry point for the Plexichat server."""
    parser = argparse.ArgumentParser(
        description="Plexichat API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                 # Start server normally
  python main.py --self-test     # Run API self-test and exit
  python main.py --create-config # Generate default config file
  python main.py --config custom.yaml # Use custom config file
  
  KEK Migration:
  python main.py --migrate-kek --kek-validate --all
  python main.py --migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
  python main.py --migrate-kek --kek-all --kek-new-env PLEXICHAT_SYSTEM_KEY
  python main.py --migrate-kek --kek-rollback --kek-keyring message_keyring.json
  python main.py --migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY --kek-dry-run
        """,
    )

    parser.add_argument("--config", help="Path to custom configuration file")
    parser.add_argument(
        "--create-config",
        action="store_true",
        help="Create default config file and exit",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Run automated API self-test and exit"
    )
    parser.add_argument("--host", help="Override server host")
    parser.add_argument("--port", type=int, help="Override server port")
    parser.add_argument("--version", action="store_true", help="Show version and exit")

    # KEK Migration arguments
    parser.add_argument(
        "--migrate-kek",
        action="store_true",
        help="Run KEK migration tool instead of starting server",
    )
    parser.add_argument(
        "--kek-keyring", help="Specific keyring to migrate (e.g., message_keyring.json)"
    )
    parser.add_argument(
        "--kek-old-env",
        help="Environment variable name for old KEK (e.g., PLEXICHAT_SYSTEM_KEY)",
    )
    parser.add_argument(
        "--kek-new-env",
        help="Environment variable name for new KEK (e.g., PLEXICHAT_MESSAGE_KEY)",
    )
    parser.add_argument(
        "--kek-all",
        action="store_true",
        help="Migrate all keyrings to new KEK (requires --kek-new-env)",
    )
    parser.add_argument(
        "--kek-validate",
        action="store_true",
        help="Validate keyrings without migration",
    )
    parser.add_argument(
        "--kek-rollback", action="store_true", help="Rollback keyring to backup"
    )
    parser.add_argument(
        "--kek-force",
        action="store_true",
        help="Force migration even if validation fails",
    )
    parser.add_argument(
        "--kek-dry-run",
        action="store_true",
        help="Validate only without making changes",
    )

    # Use parse_known_args to ignore uvicorn args if wrapped
    args, _ = parser.parse_known_args()

    if args.version:
        print(f"Plexichat Server v{VERSION}")
        return

    # Handle KEK migration
    if args.migrate_kek:
        from src.utils.encryption.kek_migration import (
            validate_keyrings,
            migrate_keyring,
            migrate_all_keyrings,
            rollback_keyring,
        )

        # Setup basic logging for migration
        import logging

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )

        if args.kek_validate:
            if args.kek_all:
                success = validate_keyrings(all_keyrings=True)
            else:
                success = validate_keyrings(all_keyrings=False)
            sys.exit(0 if success else 1)

        if args.kek_rollback:
            if not args.kek_keyring:
                print("Error: --kek-rollback requires --kek-keyring")
                sys.exit(1)
            success = rollback_keyring(args.kek_keyring)
            sys.exit(0 if success else 1)

        if args.kek_all:
            if not args.kek_new_env:
                print("Error: --kek-all requires --kek-new-env")
                sys.exit(1)
            success = migrate_all_keyrings(
                args.kek_new_env, args.kek_force, args.kek_dry_run
            )
            sys.exit(0 if success else 1)

        if args.kek_keyring:
            if not args.kek_old_env or not args.kek_new_env:
                print(
                    "Error: --kek-keyring requires both --kek-old-env and --kek-new-env"
                )
                sys.exit(1)
            success = migrate_keyring(
                args.kek_keyring,
                args.kek_old_env,
                args.kek_new_env,
                args.kek_force,
                args.kek_dry_run,
            )
            sys.exit(0 if success else 1)

        print(
            "Error: Invalid KEK migration arguments. Use --kek-validate, --kek-rollback, --kek-all, or --kek-keyring"
        )
        sys.exit(1)

    server = PlexichatServer()

    # Handle --create-config
    if args.create_config:
        config_dir = os.path.join(project_root, "config")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "config.yaml")

        if os.path.exists(config_path):
            print(f"Config file already exists at {config_path}")
            return

        import yaml

        with open(config_path, "w") as f:
            yaml.dump(server.get_default_config(), f, default_flow_style=False)
        print(f"Created default configuration at {config_path}")
        return

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
    # Priority: 1. --config arg, 2. PLEXICHAT_CONFIG env var, 3. auto-detect
    config_path = None
    config_source = None

    if args.config:
        config_path = args.config
        config_source = "command line argument"
    elif os.environ.get("PLEXICHAT_CONFIG"):
        config_path = os.environ.get("PLEXICHAT_CONFIG")
        config_source = "PLEXICHAT_CONFIG environment variable"

    if config_path:
        config.setup(
            config_path=config_path, default_config=server.get_default_config()
        )
        early_logs.append(
            ("info", f"Config loaded from {config_source}: {config_path}")
        )
    else:
        config_path = server.setup_config()
        early_logs.append(
            ("info", f"Config loaded from auto-detected path: {config_path}")
        )

    # Setup logging - NOW we can use logger
    log_config = config.get("logging", {})
    media_config = config.get("media", {})
    log_dir = media_config.get("logs_dir", os.path.join(project_root, "logs"))
    log_dir = os.path.expanduser(log_dir)  # Expand ~ to home directory

    early_logs.append(("info", "Initializing logger..."))
    early_logs.append(("info", f"  Log directory: {log_dir}"))
    early_logs.append(("info", f"  Log level: {log_config.get('level', 'INFO')}"))

    server.setup_logging()

    # Replay buffered logs
    for level, msg in early_logs:
        getattr(logger, level)(msg)

    # Now we can log normally
    logger.info("=" * 60)
    logger.info("Plexichat Server Starting")
    logger.info("=" * 60)

    # Log startup info
    app_config = config.get("application", {})
    logger.info(f"Application: {app_config.get('name', 'Plexichat')}")
    logger.info(f"Version: {VERSION}")
    logger.info(f"Environment: {app_config.get('environment', 'development')}")
    logger.info(f"Config file: {config_path}")

    # Security warnings for default/placeholder keys
    _check_security_keys()

    # Validate configuration
    try:
        server.validate_config()
        logger.info("Configuration validated successfully")
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        # Default to strict config unless explicitly disabled
        if os.getenv("NO_STRICT_CONFIG") != "true":
            sys.exit(1)

    # Setup utilities
    server.setup_utilities()

    # Initialize modules
    modules = server.initialize_modules()

    # Create application
    server.create_application(*modules)

    # Get server settings
    host = args.host or os.getenv(
        "HOST", config.get("server", {}).get("host", "127.0.0.1")
    )
    port = args.port or int(
        os.getenv("PORT", config.get("server", {}).get("port", 8000))
    )

    # Handle self-test
    if args.self_test:
        logger.info("Starting Self-Test Mode...")
        # Override host to localhost for safety
        host = "127.0.0.1"

        # Set log level to INFO for cleaner output during self-test
        import logging

        logging.getLogger("AppLogger").setLevel(logging.INFO)

        # Start server in background thread
        import uvicorn

        if server.app is None:
            logger.error("Failed to create FastAPI application for self-test")
            sys.exit(1)

        uvi_config = uvicorn.Config(server.app, host=host, port=port, log_level="error")
        uvi_server = uvicorn.Server(uvi_config)

        server_thread = threading.Thread(target=uvi_server.run, daemon=True)
        server_thread.start()

        # Wait for server to start
        import time

        time.sleep(2)

        # Run self-test
        try:
            from src.core.selftest.runner import SelfTestRunner

            runner = SelfTestRunner(base_url=f"http://{host}:{port}")
            success = runner.run_all()

            # Stop server and exit
            uvi_server.should_exit = True
            server.cleanup()
            sys.exit(0 if success else 1)
        except Exception as e:
            logger.error(f"Self-test execution failed: {e}", exc_info=True)
            sys.exit(1)

    # Run server (returns True if restart was requested)
    should_restart = server.run(host, port)

    if should_restart:
        logger.info("Restart requested, restarting server...")
        # Re-execute the script
        os.execv(sys.executable, [sys.executable] + sys.argv)

    logger.info("Server shutdown complete")


def _check_security_keys() -> None:
    """Check for default/placeholder security keys and warn if found."""
    warnings = []

    # Check media signing key
    media_config = config.get("media", {})
    signing_key = media_config.get("signing_key", "")
    if signing_key in ["", "CHANGE_THIS_SIGNING_KEY", "change-me", "changeme"]:
        warnings.append("media.signing_key is using a default/placeholder value")

    # Check Redis password (if enabled)
    redis_config = config.get("redis", {})
    if redis_config.get("enabled", False):
        redis_pass = redis_config.get("password", "")
        if not redis_pass:
            warnings.append("redis.password is empty (Redis is enabled)")

    # Check database password (if PostgreSQL)
    db_config = config.get("database", {})
    if db_config.get("type") == "postgres":
        pg_config = db_config.get("postgres", {})
        pg_pass = pg_config.get("password", "")
        if not pg_pass or pg_pass in ["password", "postgres", "changeme"]:
            warnings.append("database.postgres.password is using a weak/default value")

    # Check TURN secret (if voice enabled with TURN)
    voice_config = config.get("voice", {})
    if voice_config.get("enabled", False) and voice_config.get("turn_urls"):
        turn_secret = voice_config.get("turn_secret", "")
        if not turn_secret:
            warnings.append("voice.turn_secret is empty (TURN is configured)")

    # Check message encryption key (auto-generated)
    messaging_config = config.get("messaging", {})
    if messaging_config.get("encrypt_messages", True):
        try:
            from src.utils.encryption import is_message_key_auto_generated

            if is_message_key_auto_generated():
                warnings.append(
                    "MESSAGE ENCRYPTION: Using auto-generated key. "
                    "Set PLEXICHAT_MESSAGE_KEY env var or back up ~/.plexichat/data/message_keyring.json"
                )
        except Exception:
            warnings.append(
                "messaging.encrypt_messages is enabled (ensure PLEXICHAT_MESSAGE_KEY is set or back up message_keyring.json)"
            )

    # Log warnings
    if warnings:
        logger.warning("=" * 60)
        logger.warning("SECURITY WARNING: Default/placeholder keys detected!")
        logger.warning("=" * 60)
        for warning in warnings:
            logger.warning(f"  - {warning}")
        logger.warning(
            "Please update these values in your config file for production use."
        )
        logger.warning("=" * 60)


if __name__ == "__main__":
    main()
