# Bootstrap Package

One-shot startup helpers called during server boot.

These functions are invoked exactly once during the server startup sequence (from `cli/main.py` or programmatic entry points). They wire up the FastAPI application, configure logging, and initialize core utilities like encryption, versioning, and licensing.

## Files

- **`__init__.py`** — Empty.
- **`app.py`** — `create_application()` standalone function that wires up the FastAPI application with all modules and returns the app.
- **`logging_setup.py`** — `setup_logging()` standalone function that configures the logging subsystem from config.
- **`utilities.py`** — `setup_utilities()` standalone function that initializes validator, versioning, encryption, licensing, and keyring validation.

## Usage

```python
from src.bootstrap.app import create_application
from src.bootstrap.logging_setup import setup_logging
from src.bootstrap.utilities import setup_utilities

setup_logging("/path/to/project_root")
setup_utilities()

app = create_application(server, auth, messaging, servers, relationships,
                         presence, reactions, embeds, webhooks, settings,
                         media, search)
```

## Error Handling

- `setup_utilities()` raises `RuntimeError` with a critical message when `require_secure_source` is `True` but no secure encryption key source (TPM, HSM, environment variable) is found. This is a hard stop — the server will not start.
- `setup_utilities()` catches and re-raises exceptions from licensing initialization, ensuring licensing failures are fatal.
- `setup_logging()` creates the log directory if it does not exist; failures are logged at the system level before the logger is fully configured.
- `create_application()` does not catch exceptions from module setup — any failure propagates to the caller, which should handle startup errors centrally.

## Dependencies

- `utils.logger` — Logging subsystem configuration (rotation, levels, compression).
- `utils.config` — Configuration access for logging, encryption, and rate-limiting settings.
- `utils.validator` — Input validation setup (HTML sanitization).
- `utils.version` — Version compatibility tracking.
- `src.utils.encryption` — Encryption manager initialization (Argon2 parameters, snowflake IDs).
- `src.utils.encryption.vault` — Secure key source validation (TPM/HSM/env var).
- `src.utils.encryption.core` — Keyring file validation on disk.
- `utils.licensing` — License validation.
- `src.core.applications` — Application module initialization (OAuth, commands, interactions).
- `src.api` — API setup and FastAPI app creation.


