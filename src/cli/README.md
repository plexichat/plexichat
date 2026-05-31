# CLI Package

Command-line argument parsing and subcommand dispatch.

This package is the user-facing entry point invoked via `python main.py`. It handles argument parsing, dispatches to subcommand handlers (migration, self-test, KEK management), and orchestrates the full server startup sequence when no subcommand is given.

## Subcommands

| Subcommand | Description |
|---|---|
| `start` | Start the API server (default when no subcommand given) |
| `pre-flight` | Validate config and initialize all modules, then exit without binding to a port |
| `self-test` | Start server on `127.0.0.1`, run API self-test suite, shut down |
| `create-config` | Generate default `config/config.yaml` and exit |
| `version` | Show version string and exit |
| `rotate-secrets` | Regenerate `rate_limiting.bypass_secret` and `applications.webhook_signature_secret` |
| `migrate-db` | Run pending database migrations and exit |
| `migrate-kek` | Enter KEK key migration mode (validate, migrate, or rollback keyrings) |

### Global Options

These options are available to all subcommands:

| Option | Description |
|---|---|
| `--config PATH` | Path to custom YAML config file (default: auto-detect) |
| `--host HOST` | Override server bind address (default: `127.0.0.1`) |
| `--port PORT` | Override server port (default: `8000`) |

## Files

- **`__init__.py`** — Empty.
- **`main.py`** — `parse_args()` (argparser definition with subcommand support) and `main()` (top-level orchestrator that dispatches to handlers).
- **`migrate_db.py`** — `handle_migrate_db()` — runs database migrations and exits.
- **`migrate_kek.py`** — `handle_migrate_kek()` — handles KEK migration/validation/rollback.
- **`selftest.py`** — `handle_selftest()` — starts the server in a background thread, runs the self-test suite, cleans up, and exits.

## Usage

```bash
# Start server normally
python main.py

# Validate configuration without starting
python main.py pre-flight

# Show help for a specific subcommand
python main.py start --help
python main.py pre-flight --help
python main.py migrate-kek --help

# Run self-test suite
python main.py self-test

# Create default config file
python main.py create-config

# Rotate secrets
python main.py rotate-secrets

# Run database migrations
python main.py migrate-db

# KEK operations
python main.py migrate-kek --kek-validate --kek-all
python main.py migrate-kek --kek-keyring message_keyring.json \
    --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
python main.py migrate-kek --kek-rollback --kek-keyring message_keyring.json
python main.py migrate-kek --kek-dry-run --kek-keyring message_keyring.json \
    --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
```

Programmatic invocation:

```python
from src.cli.main import parse_args, main as cli_main

args = parse_args(["pre-flight", "--config", "my-config.yaml"])
cli_main(["pre-flight", "--config", "my-config.yaml"])
```

## Pre-Flight Mode

The `pre-flight` subcommand runs through the entire server startup sequence:

1. Create home directories (`~/.plexichat/data`, `logs`, `media`, `temp`, `config`)
2. Load configuration (from `--config`, `PLEXICHAT_CONFIG` env var, or auto-detect)
3. Setup logging
4. Security key checks (warns about weak/default keys)
5. Configuration type validation
6. Utilities setup (validator, encryption, licensing, keyrings)
7. Module initialization (database, auth, messaging, servers, etc.)
8. Application creation (FastAPI app with all routes)

After all checks pass, it prints a success message and exits with code 0 without binding to any port. If any step fails, it exits with code 1.

## Error Handling

- `main()` calls `sys.exit(1)` if `validate_config()` fails and `NO_STRICT_CONFIG` is not set.
- `handle_migrate_db()` calls `sys.exit(1)` when migrations fail.
- `handle_migrate_kek()` prints error messages to stderr and exits with code 1 for invalid argument combinations (e.g., `--kek-rollback` without `--kek-keyring`).
- `handle_selftest()` exits with code 1 if the FastAPI app is `None` or if the self-test runner raises any exception.
- The `pre-flight` subcommand exits with code 1 if any startup step fails.

## Dependencies

- `argparse` — Standard library argument parsing.
- `src.server` — `PlexichatServer`, `setup_config`, `initialize_modules`, `_check_security_keys`.
- `src.bootstrap` — `setup_logging`, `setup_utilities`, `create_application`.
- `src.cli.migrate_db` — Database migration handler.
- `src.cli.migrate_kek` — KEK migration handler (delegates to `src.utils.encryption.kek_migration`).
- `src.cli.selftest` — Self-test handler (delegates to `src.core.selftest.runner`).
- `yaml` — Configuration file serialization for `create-config` and `rotate-secrets`.
- `uvicorn` — ASGI server used during self-test mode.
