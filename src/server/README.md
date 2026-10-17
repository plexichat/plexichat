# Server package

The server package is the entry point for all server-side orchestration. It provides the `PlexichatServer` class that manages startup, runtime, and graceful shutdown, along with standalone utilities for configuration loading, module bootstrapping, and security key validation.

## Files

- **`__init__.py`** -- re-exports `PlexichatServer`.
- **`lifecycle.py`** -- `PlexichatServer` class with `__init__`, `run()`, `cleanup()`, `notify_clients_shutdown()`, `notify_clients_restart()`, `validate_config()`, and `get_default_config()`. Also defines `VERSION`.
- **`config_loader.py`** -- `setup_config()` and `_apply_env_overrides()` functions for loading configuration and applying environment variable overrides. Env-var coercion is deduplicated through shared helpers (`_as_bool`, `_apply_int`, `_apply_float`, `_apply_bool`).
- **`initializer.py`** -- `initialize_modules()` function that bootstraps all core modules (database, auth, messaging, servers, and others) in dependency order.
- **`security_checks.py`** -- `_check_security_keys()` function that warns about default or placeholder credentials.

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

## Error handling

- `validate_config()` raises `ValueError` when configuration types mismatch the expected schema (for example, a string where an integer is expected).
- `run()` handles TLS configuration failures gracefully, logging errors and falling back to HTTP.
- Signal handlers (`SIGINT`/`SIGTERM`) trigger graceful shutdown; a second signal forces `os._exit(1)`.
- `notify_clients_shutdown()` and `notify_clients_restart()` catch all exceptions internally (logged at debug level) so a failure to broadcast does not block cleanup.
- `cleanup()` catches and logs errors from database closure and session cleanup without re-raising.

## Dependencies

- `utils.config` -- global configuration access and environment variable overrides
- `utils.logger` -- logging subsystem
- `utils.version` -- version string management
- `src.core.database` -- database connection lifecycle
- `src.core.tls` -- optional TLS/SSL configuration for HTTPS
- `src.api` -- WebSocket session management for client notifications
- `uvicorn` -- ASGI server used in `run()`
