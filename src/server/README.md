# Server Package

Core server lifecycle, configuration, initialization, and security.

This package is the entry point for all server-side orchestration. It provides the `PlexichatServer` class that manages startup, runtime, and graceful shutdown, along with standalone utilities for config loading, module bootstrapping, and security key validation.

## Files

- **`__init__.py`** — Re-exports `PlexichatServer`.
- **`lifecycle.py`** — `PlexichatServer` class with `__init__`, `run()`, `cleanup()`, `notify_clients_shutdown()`, `notify_clients_restart()`, `validate_config()`, `get_default_config()`. Also defines `VERSION`.
- **`config_loader.py`** — `setup_config()` and `_apply_env_overrides()` standalone functions for configuration loading and environment variable overrides.
- **`initializer.py`** — `initialize_modules()` standalone function that bootstraps all core modules (database, auth, messaging, servers, etc.) in dependency order.
- **`security_checks.py`** — `_check_security_keys()` standalone function that warns about default/placeholder credentials.

## Usage

```python
from src.server import PlexichatServer

server = PlexichatServer()
server.app = app  # FastAPI application from create_application()
should_restart = server.run(host="0.0.0.0", port=8000)
```

For programmatic use with config:

```python
server = PlexichatServer()
defaults = server.get_default_config()
# Merge with custom values, then pass to setup_config
config_path = setup_config("/path/to/project", defaults)
server.validate_config()  # raises ValueError on type mismatches
```

## Error Handling

- `validate_config()` raises `ValueError` when configuration types mismatch the expected schema (e.g., a string where an integer is expected).
- `run()` handles TLS configuration failures gracefully, logging errors and falling back to HTTP.
- Signal handlers (SIGINT/SIGTERM) trigger graceful shutdown; a second signal forces `os._exit(1)`.
- `notify_clients_shutdown()` and `notify_clients_restart()` catch all exceptions internally (logged at debug level) so a failure to broadcast does not block cleanup.
- `cleanup()` catches and logs errors from database closure and session cleanup without re-raising.

## Dependencies

- `utils.config` — Global configuration access and environment variable overrides.
- `utils.logger` — Logging subsystem.
- `utils.version` — Version string management.
- `src.core.database` — Database connection lifecycle.
- `src.core.tls` — Optional TLS/SSL configuration for HTTPS.
- `src.api` — WebSocket session management for client notifications.
- `uvicorn` — ASGI server used in `run()`.


