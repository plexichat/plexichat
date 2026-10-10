# CLI package

Command-line parsing and subcommand dispatch. This package is the entry point invoked as `python main.py`. It parses arguments, dispatches to subcommand handlers, and runs the full server startup sequence when no subcommand is given.

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `start` | Start the API server. This is the default when you omit the subcommand. |
| `pre-flight` | Validate configuration and initialize all modules, then exit without binding to a port |
| `self-test` | Start the server on `127.0.0.1`, run the API self-test suite, then shut down |
| `create-config` | Generate `config/config.yaml` with defaults and exit |
| `version` | Print the version string and exit |
| `rotate-secrets` | Regenerate `applications.webhook_signature_secret` |
| `migrate-db` | Run pending database migrations and exit |
| `migrate-kek` | Manage Key Encryption Key (KEK) migration: validate, migrate, or rollback keyrings |

### Global options

These options are available for every subcommand and must appear before the subcommand:

| Option | Description |
|--------|-------------|
| `--config PATH` | Path to a custom YAML config file. Default is auto-detected (`~/.plexichat/config/config.yaml` or `./config/config.yaml`). |
| `--host HOST` | Override the bind address. Default is `127.0.0.1`. |
| `--port PORT` | Override the bind port. Default is `8000`. |

## Files

| File | Export | Description |
|------|--------|-------------|
| `__init__.py` | — | Empty package marker |
| `main.py` | `parse_args(argv)`, `main(argv)` | Argument parser and top-level dispatcher |
| `migrate_db.py` | `handle_migrate_db(project_root, dry_run)` | Database migration runner |
| `migrate_kek.py` | `handle_migrate_kek(args)` | KEK migration, validation, and rollback |
| `selftest.py` | `handle_selftest(server, host, port, ...)` | In-process self-test runner |

## Usage

```bash
# Start the server normally
python main.py
python main.py start --host 0.0.0.0 --port 8000

# Validate configuration without binding to a port
python main.py pre-flight
python main.py pre-flight --config my-config.yaml

# Show help for a subcommand
python main.py start --help
python main.py migrate-kek --help

# Run the API self-test suite
python main.py self-test
python main.py self-test --unlicensed

# Generate a default config file
python main.py create-config

# Rotate the webhook signature secret
python main.py rotate-secrets

# Run database migrations
python main.py migrate-db
python main.py migrate-db --dry-run

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

Unknown subcommands produce a `Did you mean ...?` suggestion based on close matches.

## Pre-flight mode

`python main.py pre-flight` runs the full startup sequence without opening a socket:

1. Creates home directories (`~/.plexichat/data`, `logs`, `media`, `temp`, `config`).
2. Loads configuration from `--config`, `PLEXICHAT_CONFIG`, or auto-detection.
3. Configures logging via `src.bootstrap.logging_setup.setup_logging`.
4. Checks security keys and auto-generates `media.signing_key` if it is a placeholder.
5. Validates the configuration structure.
6. Initializes utilities (validator, encryption, licensing, keyrings).
7. Initializes all core modules via `src.server.initializer.initialize_modules`.
8. Creates the FastAPI application via `src.bootstrap.app.create_application`.

If every step succeeds, the command prints a success message and exits with status `0`. If any step fails, it exits with status `1`. No port is bound, so you can run it on a machine that already serves traffic.

When the server does start (`start` or the default), it serves:

- `GET /` — API root.
- `GET /health` — Root health proxy.
- `GET /api/v1/health` — Full health including version and event metrics.
- `GET /api/v1/ready` — Readiness probe returning `{"status":"ready"}`.
- WebSocket ` /gateway` — Real-time gateway (see `src/api/websocket/README.md` for opcodes `50` and `60`–`67` and intents `1 << 20` / `1 << 21`).

## Error handling

- `main()` exits with status `1` if `validate_config()` fails and `NO_STRICT_CONFIG` is not `true`.
- `handle_migrate_db()` exits with status `1` when migrations fail.
- `handle_migrate_kek()` prints to stderr and exits with status `1` for invalid combinations (for example, `--kek-rollback` without `--kek-keyring`).
- `handle_selftest()` exits with status `1` if the app is `None` or the test runner raises.
- `pre-flight` exits with status `1` if any startup step raises.

## Dependencies

- `argparse` — Argument parsing (with close-match suggestions for typos).
- `src.server.PlexichatServer`, `src.server.config_loader.setup_config`, `src.server.security_checks._check_security_keys` — Server lifecycle and config.
- `src.server.initializer.initialize_modules` — Core module initialization.
- `src.bootstrap.logging_setup.setup_logging`, `src.bootstrap.utilities.setup_utilities`, `src.bootstrap.app.create_application` — Bootstrap helpers.
- `src.cli.migrate_db` — Delegates to database migration logic.
- `src.cli.migrate_kek` — Delegates to `src.utils.encryption.kek_migration`.
- `src.cli.selftest` — Delegates to `src.core.selftest.runner`.
- `yaml` — Reads and writes `config.yaml`.
- `uvicorn` — ASGI server for `self-test` mode.

## Related

- `src/bootstrap/README.md` — One-shot startup helpers called by `_run_startup`.
- `src/api/websocket/README.md` — Gateway endpoint and real-time protocol.
- `src/api/routes/health.py` — Implementation of `/api/v1/health` and `/api/v1/ready`.
