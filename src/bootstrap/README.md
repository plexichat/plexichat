# Bootstrap package

One-shot startup helpers that run exactly once when the server boots. Use them from `src/cli/main.py` or programmatic entry points to wire the FastAPI application, configure logging, and initialize core utilities.

## Files

| File | Export | Description |
|------|--------|-------------|
| `__init__.py` | — | Empty package marker |
| `app.py` | `create_application(server, ...)` | Creates the FastAPI application and mounts all routes |
| `logging_setup.py` | `setup_logging(project_root)` | Configures logging from `config.yaml` |
| `utilities.py` | `setup_utilities()` | Initializes validator, versioning, encryption, licensing, and keyring |

## Usage

```python
from src.bootstrap.app import create_application
from src.bootstrap.logging_setup import setup_logging
from src.bootstrap.utilities import setup_utilities

setup_logging("/path/to/project_root")
setup_utilities()

app = create_application(
    server, auth, messaging, servers, relationships,
    presence, reactions, embeds, webhooks, settings,
    media, search,
)
```

Call `setup_logging` before `setup_utilities` so early startup messages respect the configured level and rotation. Call `setup_utilities` before `create_application` so encryption and validation are ready when routes initialize.

### What `create_application` mounts

`create_application` calls `src.api.setup` and `src.api.create_app`. The resulting FastAPI app includes:

- REST API under `config.api_prefix` (default `/api/v1`).
- WebSocket gateway at `/gateway` (top-level, no `/api/v1` prefix). See `src/api/websocket/README.md` for opcodes `50`, `60`–`67` and intents `1 << 20` / `1 << 21`.
- Health probes:
  - `GET /api/v1/health` — Full health check with version and event metrics.
  - `GET /api/v1/ready` — Minimal readiness probe (`{"status":"ready"}`) for orchestrators.
  - `GET /health` — Root proxy to `GET /api/v1/health`.
  - `GET /` — API root with name, version, and docs links.

## Error handling

- `setup_utilities()` raises `RuntimeError` when `require_secure_source` is `true` but no secure encryption key source (TPM, HSM, or environment variable) exists. The server does not start.
- `setup_utilities()` re-raises licensing initialization failures. Licensing failures are fatal.
- `setup_logging()` creates the log directory if it does not exist. Failures before the logger is ready are recorded at the system level.
- `create_application()` does not catch module setup failures. The caller must handle startup errors centrally (see `src/cli/main.py:_run_startup`).

## Dependencies

- `utils.logger` — Log rotation, levels, compression.
- `utils.config` — Configuration for logging, encryption, rate limiting, and API prefix.
- `utils.validator` — HTML sanitization setup.
- `utils.version` — Version compatibility tracking.
- `src.utils.encryption` — Encryption manager (Argon2 parameters, Snowflake IDs).
- `src.utils.encryption.vault` — Secure key source validation (TPM, HSM, env var).
- `src.utils.encryption.core` — Keyring file validation on disk.
- `utils.licensing` — License validation.
- `src.core.applications` — OAuth, commands, interactions setup.
- `src.api` — FastAPI factory, route registration, and gateway setup.
- `src.api.websocket` — Gateway router mounted at `/gateway`.
- `src.api.routes.health` — Health and readiness handlers for `/api/v1/health` and `/api/v1/ready`.

## Related

- `src/cli/README.md` — CLI startup sequence that calls these helpers.
- `src/api/app.py` — FastAPI factory that defines the actual routes.
- `src/api/websocket/README.md` — Gateway intents and opcodes.
