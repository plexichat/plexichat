# CLI Package

Command-line argument parsing and subcommand dispatch.

This package is the user-facing entry point invoked via `python main.py`. It handles argument parsing, dispatches to subcommand handlers (migration, self-test, KEK management), and orchestrates the full server startup sequence when no subcommand is given.

## Files

- **`__init__.py`** — Empty.
- **`main.py`** — `parse_args()` (argparser definition) and `main()` (top-level orchestrator that dispatches to handlers or starts the server).
- **`migrate_db.py`** — `handle_migrate_db()` — runs database migrations and exits.
- **`migrate_kek.py`** — `handle_migrate_kek()` — handles KEK migration/validation/rollback.
- **`selftest.py`** — `handle_selftest()` — starts the server in a background thread, runs the self-test suite, cleans up, and exits.

## Usage

```bash
# Start server normally
python main.py

# Run self-test suite
python main.py --self-test

# Create default config file
python main.py --create-config

# Run database migrations
python main.py --migrate-db

# KEK operations
python main.py --migrate-kek --kek-validate --all
python main.py --migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
python main.py --migrate-kek --kek-rollback --kek-keyring message_keyring.json
```

Programmatic invocation:

```python
from src.cli.main import parse_args, main as cli_main

args = parse_args()
cli_main()  # dispatches based on parsed args
```

## Error Handling

- `main()` calls `sys.exit(1)` if `validate_config()` fails and `NO_STRICT_CONFIG` is not set.
- `handle_migrate_db()` calls `sys.exit(1)` when migrations fail.
- `handle_migrate_kek()` prints error messages to stderr and exits with code 1 for invalid argument combinations (e.g., `--kek-rollback` without `--kek-keyring`).
- `handle_selftest()` exits with code 1 if the FastAPI app is `None` or if the self-test runner raises any exception.
- `main()` uses `os.execv()` for restart — this replaces the current process. Any failure before `execv` (e.g., port binding failure in uvicorn) propagates as an unhandled exception.
- Line count and pagination arguments for `--help` are handled by argparse's built-in formatter.

## Dependencies

- `argparse` — Standard library argument parsing.
- `src.server` — `PlexichatServer`, `setup_config`, `initialize_modules`, `_check_security_keys`.
- `src.bootstrap` — `setup_logging`, `setup_utilities`, `create_application`.
- `src.cli.migrate_db` — Database migration handler.
- `src.cli.migrate_kek` — KEK migration handler (delegates to `src.utils.encryption.kek_migration`).
- `src.cli.selftest` — Self-test handler (delegates to `src.core.selftest.runner`).
- `yaml` — Configuration file serialization for `--create-config` and `--rotate-secrets`.
- `uvicorn` — ASGI server used during self-test mode.
