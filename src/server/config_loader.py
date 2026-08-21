import os
import urllib.parse
from pathlib import Path
from typing import Optional

import utils.logger as logger
import utils.config as config


def _as_bool(raw: str) -> bool:
    """Coerce an env-var string to a boolean (``true``/``1``/``yes``)."""
    return raw.lower() in ("true", "1", "yes")


def _apply_int(
    target: dict,
    key: str,
    raw: Optional[str],
    env_name: str,
    *,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> bool:
    """Assign ``raw`` coerced to ``int`` into ``target[key]``.

    Unset/falsy values are ignored so the existing default is preserved.
    Unparseable or out-of-range values log a warning and are ignored.
    Returns ``True`` when a value was actually assigned.
    """
    if not raw:
        return False
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Invalid {env_name} value: {raw}")
        return False
    if min_value is not None and value < min_value:
        logger.warning(f"Invalid {env_name} value: {raw} (must be >= {min_value})")
        return False
    if max_value is not None and value > max_value:
        logger.warning(f"Invalid {env_name} value: {raw} (must be <= {max_value})")
        return False
    target[key] = value
    return True


def _apply_float(target: dict, key: str, raw: Optional[str], env_name: str) -> bool:
    """Assign ``raw`` coerced to ``float`` into ``target[key]``.

    Unset/falsy values are ignored; unparseable values log a warning.
    Returns ``True`` when a value was actually assigned.
    """
    if not raw:
        return False
    try:
        target[key] = float(raw)
    except ValueError:
        logger.warning(f"Invalid {env_name} value: {raw}")
        return False
    return True


def _apply_bool(target: dict, key: str, raw: Optional[str]) -> bool:
    """Assign ``raw`` coerced to ``bool`` into ``target[key]``.

    Unset/falsy values are ignored. Returns ``True`` when a value was
    actually assigned.
    """
    if not raw:
        return False
    target[key] = _as_bool(raw)
    return True


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

        _apply_int(
            postgres_config,
            "port",
            os.getenv("POSTGRES_PORT"),
            "POSTGRES_PORT",
            min_value=1,
            max_value=65535,
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

    _apply_int(
        pool_config,
        "min_connections",
        os.getenv("DB_POOL_MIN_CONNECTIONS"),
        "DB_POOL_MIN_CONNECTIONS",
        min_value=0,
    )
    _apply_int(
        pool_config,
        "max_connections",
        os.getenv("DB_POOL_MAX_CONNECTIONS"),
        "DB_POOL_MAX_CONNECTIONS",
        min_value=0,
    )
    _apply_int(
        pool_config,
        "connect_timeout",
        os.getenv("DB_POOL_CONNECT_TIMEOUT"),
        "DB_POOL_CONNECT_TIMEOUT",
        min_value=0,
    )
    _apply_int(
        pool_config,
        "max_idle_time",
        os.getenv("DB_POOL_MAX_IDLE_TIME"),
        "DB_POOL_MAX_IDLE_TIME",
    )
    _apply_int(
        pool_config,
        "validation_interval",
        os.getenv("DB_POOL_VALIDATION_INTERVAL"),
        "DB_POOL_VALIDATION_INTERVAL",
    )
    _apply_bool(
        pool_config,
        "enable_validation",
        os.getenv("DB_POOL_ENABLE_VALIDATION"),
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

    _apply_bool(monitoring_config, "enabled", os.getenv("MONITORING_ENABLED"))
    _apply_int(
        monitoring_config,
        "log_interval",
        os.getenv("MONITORING_LOG_INTERVAL"),
        "MONITORING_LOG_INTERVAL",
    )
    _apply_bool(
        monitoring_config,
        "metrics_enabled",
        os.getenv("MONITORING_METRICS_ENABLED"),
    )

    if "alert_thresholds" not in monitoring_config:
        monitoring_config["alert_thresholds"] = {}

    alert_thresholds = monitoring_config["alert_thresholds"]

    _apply_float(
        alert_thresholds,
        "cpu_percent",
        os.getenv("MONITORING_ALERT_CPU_THRESHOLD"),
        "MONITORING_ALERT_CPU_THRESHOLD",
    )
    _apply_float(
        alert_thresholds,
        "memory_percent",
        os.getenv("MONITORING_ALERT_MEMORY_THRESHOLD"),
        "MONITORING_ALERT_MEMORY_THRESHOLD",
    )
    _apply_float(
        alert_thresholds,
        "db_pool_saturation_percent",
        os.getenv("MONITORING_ALERT_DB_POOL_THRESHOLD"),
        "MONITORING_ALERT_DB_POOL_THRESHOLD",
    )
    _apply_int(
        alert_thresholds,
        "query_time_ms",
        os.getenv("MONITORING_ALERT_QUERY_TIME_MS"),
        "MONITORING_ALERT_QUERY_TIME_MS",
    )
    _apply_int(
        alert_thresholds,
        "db_errors_per_minute",
        os.getenv("MONITORING_ALERT_DB_ERRORS_PER_MINUTE"),
        "MONITORING_ALERT_DB_ERRORS_PER_MINUTE",
    )
    _apply_int(
        alert_thresholds,
        "api_response_time_ms",
        os.getenv("MONITORING_ALERT_API_RESPONSE_TIME_MS"),
        "MONITORING_ALERT_API_RESPONSE_TIME_MS",
    )
    _apply_float(
        alert_thresholds,
        "error_rate_percent",
        os.getenv("MONITORING_ALERT_ERROR_RATE_PERCENT"),
        "MONITORING_ALERT_ERROR_RATE_PERCENT",
    )
    _apply_int(
        alert_thresholds,
        "active_connections",
        os.getenv("MONITORING_ALERT_ACTIVE_CONNECTIONS"),
        "MONITORING_ALERT_ACTIVE_CONNECTIONS",
    )

    config.set("monitoring", monitoring_config)

    log_level = os.getenv("LOG_LEVEL")
    if log_level:
        log_config = config.get("logging", {})
        log_config["level"] = log_level.upper()
        config.set("logging", log_config)

    media_config = config.get("media", {})

    _apply_bool(media_config, "scanner_enabled", os.getenv("SCANNER_ENABLED"))

    scanner_host = os.getenv("SCANNER_HOST")
    if scanner_host:
        media_config["scanner_host"] = scanner_host

    _apply_int(
        media_config,
        "scanner_port",
        os.getenv("SCANNER_PORT"),
        "SCANNER_PORT",
        min_value=1,
        max_value=65535,
    )

    config.set("media", media_config)

    valkey_config = config.get("valkey") or config.get("redis") or {}

    valkey_changed = False

    valkey_enabled = os.getenv("VALKEY_ENABLED")
    if valkey_enabled is not None:
        # Booleans can't ride the ${VAR:-default} interpolation (it yields a
        # string, and "false" would be truthy), so the flag is the one value
        # applied here, mirroring MONITORING_ENABLED / SCANNER_ENABLED.
        valkey_config["enabled"] = _as_bool(valkey_enabled)
        valkey_changed = True

    # Port/db/max_connections are declared as ints in the defaults (matching
    # what config files and the valkey client expect), so they can't ride
    # ${VAR:-default} interpolation either. Apply env overrides with the same
    # int coercion used for SCANNER_PORT / postgres ports above.
    valkey_changed |= _apply_int(
        valkey_config,
        "port",
        os.getenv("VALKEY_PORT"),
        "VALKEY_PORT",
        min_value=1,
        max_value=65535,
    )
    valkey_changed |= _apply_int(
        valkey_config, "db", os.getenv("VALKEY_DB"), "VALKEY_DB"
    )

    valkey_max_connections = os.getenv("VALKEY_CONNECTION_POOL")
    if valkey_max_connections:
        conn_pool = valkey_config.setdefault("connection_pool", {})
        valkey_changed |= _apply_int(
            conn_pool,
            "max_connections",
            valkey_max_connections,
            "VALKEY_CONNECTION_POOL",
        )

    # Only persist when an override was actually applied: the write is
    # atomic and thread-safe now, but an unconditional rewrite of the
    # home config on every boot is wasteful and churns the file.

    # --- Search backend overrides (low-resource default: postgres/sqlite, optional meilisearch/elastic) ---
    search_config = config.get("search", {}) or {}
    search_changed = False
    backend_env = os.getenv("SEARCH_BACKEND")
    if backend_env:
        be = backend_env.strip().lower()
        if be in (
            "sqlite_fts5",
            "sqlite",
            "postgres",
            "meilisearch",
            "elasticsearch",
            "es",
        ):
            # normalize
            if be == "sqlite":
                be = "sqlite_fts5"
            if be == "es":
                be = "elasticsearch"
            search_config["backend"] = be
            search_changed = True
        else:
            import utils.logger as _slog

            _slog.warning(f"Invalid SEARCH_BACKEND value: {backend_env}")
    enabled = os.getenv("SEARCH_ENABLED")
    if enabled is not None:
        search_config["enabled"] = _as_bool(enabled)
        search_changed = True
    search_changed |= _apply_int(
        search_config,
        "result_limit",
        os.getenv("SEARCH_RESULT_LIMIT"),
        "SEARCH_RESULT_LIMIT",
        min_value=1,
        max_value=1000,
    )
    search_changed |= _apply_int(
        search_config,
        "batch_size",
        os.getenv("SEARCH_BATCH_SIZE"),
        "SEARCH_BATCH_SIZE",
        min_value=1,
        max_value=1000,
    )
    wti_raw = os.getenv("SEARCH_WRITE_TIME_INDEXING")
    if wti_raw is not None:
        search_config["write_time_indexing"] = _as_bool(wti_raw)
        search_changed = True
    # Per-backend envs
    meili_host = os.getenv("SEARCH_MEILISEARCH_HOST") or os.getenv("MEILISEARCH_HOST")
    meili_key = os.getenv("SEARCH_MEILISEARCH_API_KEY") or os.getenv(
        "MEILISEARCH_API_KEY"
    )
    if meili_host or meili_key is not None:
        ms = search_config.setdefault("meilisearch", {})
        if meili_host:
            ms["host"] = meili_host
            search_changed = True
        if meili_key is not None:
            ms["api_key"] = meili_key or None
            search_changed = True
    es_hosts = os.getenv("SEARCH_ELASTICSEARCH_HOSTS") or os.getenv(
        "ELASTICSEARCH_HOSTS"
    )
    if es_hosts:
        es = search_config.setdefault("elasticsearch", {})
        # comma separated list
        hosts = [h.strip() for h in es_hosts.split(",") if h.strip()]
        if hosts:
            es["hosts"] = hosts
            search_changed = True
    if search_changed:
        config.set("search", search_config)

    if valkey_changed:
        config.set("valkey", valkey_config)


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
