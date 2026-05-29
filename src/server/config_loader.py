import os
import urllib.parse
from pathlib import Path
from typing import Optional

import utils.logger as logger
import utils.config as config


def _apply_env_overrides() -> None:
    db_config = config.get("database", {})

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgres://") or database_url.startswith(
            "postgresql://"
        ):
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
            if parsed.query:
                params = urllib.parse.parse_qs(parsed.query)
                if "sslmode" in params:
                    db_config["postgres"]["sslmode"] = params["sslmode"][0]
        elif database_url.startswith("sqlite:///"):
            db_config["type"] = "sqlite"
            db_config["path"] = database_url[10:]
    else:
        if not db_config.get("postgres"):
            db_config["postgres"] = {}

        postgres_config = db_config["postgres"]

        postgres_host = os.getenv("POSTGRES_HOST")
        if postgres_host:
            postgres_config["host"] = postgres_host

        postgres_port = os.getenv("POSTGRES_PORT")
        if postgres_port:
            try:
                port_val = int(postgres_port)
                if port_val < 1 or port_val > 65535:
                    raise ValueError(
                        f"Port must be between 1 and 65535, got {port_val}"
                    )
                postgres_config["port"] = port_val
            except ValueError as e:
                logger.warning(
                    f"Invalid POSTGRES_PORT value: {postgres_port}, using default: {e}"
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

    if "connection_pool" not in db_config:
        db_config["connection_pool"] = {}

    pool_config = db_config["connection_pool"]

    db_pool_min = os.getenv("DB_POOL_MIN_CONNECTIONS")
    if db_pool_min:
        try:
            val = int(db_pool_min)
            if val < 0:
                raise ValueError(f"Value must be >= 0, got {val}")
            pool_config["min_connections"] = val
        except ValueError as e:
            logger.warning(f"Invalid DB_POOL_MIN_CONNECTIONS value: {db_pool_min}: {e}")

    db_pool_max = os.getenv("DB_POOL_MAX_CONNECTIONS")
    if db_pool_max:
        try:
            val = int(db_pool_max)
            if val < 0:
                raise ValueError(f"Value must be >= 0, got {val}")
            pool_config["max_connections"] = val
        except ValueError as e:
            logger.warning(f"Invalid DB_POOL_MAX_CONNECTIONS value: {db_pool_max}: {e}")

    db_pool_timeout = os.getenv("DB_POOL_CONNECT_TIMEOUT")
    if db_pool_timeout:
        try:
            val = int(db_pool_timeout)
            if val < 0:
                raise ValueError(f"Value must be >= 0, got {val}")
            pool_config["connect_timeout"] = val
        except ValueError as e:
            logger.warning(
                f"Invalid DB_POOL_CONNECT_TIMEOUT value: {db_pool_timeout}: {e}"
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

    if "alert_thresholds" not in monitoring_config:
        monitoring_config["alert_thresholds"] = {}

    alert_thresholds = monitoring_config["alert_thresholds"]

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
            alert_thresholds["db_pool_saturation_percent"] = float(db_pool_threshold)
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

    log_level = os.getenv("LOG_LEVEL")
    if log_level:
        log_config = config.get("logging", {})
        log_config["level"] = log_level.upper()
        config.set("logging", log_config)


def setup_config(
    project_root: str, default_config: dict, config_path_arg: Optional[str] = None
) -> str:
    config_paths = [
        (
            str(Path.home() / ".plexichat" / "config" / "config.yaml"),
            "home directory",
        ),
        (os.path.join(project_root, "config", "config.yaml"), "project directory"),
    ]

    config_path = None
    for cp, source in config_paths:
        if os.path.exists(cp):
            config_path = str(cp)
            break

    if not config_path:
        config_path = str(config_paths[0][0])

    config.setup(config_path=config_path, default_config=default_config)

    _apply_env_overrides()

    return config_path
