---
sidebar_position: 1
title: CLI Reference
description: Command-line interface reference for the Plexichat server
---

# Plexichat CLI Reference

The Plexichat server is managed through a single entry point — `python main.py` — with a set of subcommands for common operations.

---

## Usage

```bash
python main.py [global-options] <subcommand> [subcommand-options]
```

Run `python main.py --help` to see all subcommands, or `python main.py <subcommand> --help` for subcommand-specific options.

---

## Global Options

These options can be placed before any subcommand and apply to all of them.

| Option | Description |
|---|---|
| `--config PATH` | Path to a custom YAML configuration file. If not provided, the server auto-detects `~/.plexichat/config/config.yaml` or `./config/config.yaml` (in that order). |
| `--host HOST` | Override the server bind address. Default: `127.0.0.1` |
| `--port PORT` | Override the server port. Default: `8000` |

---

## Subcommands

### `start`

Start the Plexichat API server. This is the **default** — running `python main.py` without a subcommand is equivalent to `python main.py start`.

```bash
python main.py start
python main.py start --host 0.0.0.0 --port 8080
python main.py start --config production.yaml
```

The server will:
1. Create data directories under `~/.plexichat/`
2. Load configuration
3. Set up logging
4. Validate security keys and configuration
5. Initialize database (with migrations), auth, messaging, and all core modules
6. Create the FastAPI application with all routes
7. Start listening on the configured host and port

---

### `pre-flight`

Validate the entire configuration and module initialization **without binding to a port**. This is useful for CI/CD pipelines, deployment scripts, or verifying a config change before restarting the server.

```bash
python main.py pre-flight
python main.py pre-flight --config staging.yaml
```

The pre-flight check runs through the full startup sequence:
1. Directory creation
2. Configuration loading and validation
3. Security key checks (warns about weak/default keys)
4. Configuration type structure validation
5. Encryption, licensing, and keyring setup
6. Database connection and migration
7. All module initializations (auth, messaging, servers, etc.)
8. FastAPI application creation

On success it prints a confirmation and exits with code **0**. On any failure it exits with code **1**.

---

### `self-test`

Start the server on `127.0.0.1`, run the automated API self-test suite, then shut down.

```bash
python main.py self-test
```

The self-test validates that all API endpoints respond correctly. It starts uvicorn in a background thread, runs every test in the self-test runner, and exits with code **0** on success or **1** on failure.

---

### `create-config`

Generate a default `config/config.yaml` file with all settings at their factory defaults.

```bash
python main.py create-config
```

If the file already exists, the command does nothing and exits to avoid overwriting an existing configuration.

---

### `version`

Print the current Plexichat server version and exit.

```bash
python main.py version
# Output: Plexichat Server a.1.0-103
```

---

### `rotate-secrets`

Regenerate the `rate_limiting.bypass_secret` and `applications.webhook_signature_secret` values in the config file with new cryptographically random values.

```bash
python main.py rotate-secrets
python main.py rotate-secrets --config config/production.yaml
```

This updates the config file **in-place**. A backup is recommended before running this in production.

---

### `migrate-db`

Connect to the database, apply all pending migrations, print a summary, and exit.

```bash
python main.py migrate-db
```

Exits with code **0** on success, **1** if any migration fails.

---

### `migrate-kek`

Manage Key Encryption Key (KEK) migrations — validate, migrate, or rollback keyrings.

```bash
python main.py migrate-kek --kek-validate --kek-all
python main.py migrate-kek --kek-keyring message_keyring.json \
    --kek-old-env PLEXICHAT_SYSTEM_KEY \
    --kek-new-env PLEXICHAT_MESSAGE_KEY
python main.py migrate-kek --kek-all --kek-new-env PLEXICHAT_SYSTEM_KEY
python main.py migrate-kek --kek-rollback --kek-keyring message_keyring.json
```

**KEK Options:**

| Option | Description |
|---|---|
| `--kek-keyring FILE` | Specific keyring to migrate (e.g., `message_keyring.json`) |
| `--kek-old-env VAR` | Environment variable name for the old KEK (e.g., `PLEXICHAT_SYSTEM_KEY`) |
| `--kek-new-env VAR` | Environment variable name for the new KEK (e.g., `PLEXICHAT_MESSAGE_KEY`) |
| `--kek-all` | Migrate all keyrings to the new KEK (requires `--kek-new-env`) |
| `--kek-validate` | Validate keyring integrity without making changes |
| `--kek-rollback` | Rollback a keyring from its backup file |
| `--kek-force` | Force migration even if validation checks fail |
| `--kek-dry-run` | Simulate migration without writing any changes |

---

## Environment Variables

In addition to CLI options, several environment variables influence server behavior:

| Variable | Overrides |
|---|---|
| `PLEXICHAT_CONFIG` | Config file path (alternative to `--config`) |
| `PLEXICHAT_SYSTEM_KEY` | Root system encryption key |
| `PLEXICHAT_MESSAGE_KEY` | Message encryption key |
| `PLEXICHAT_SMTP_PASSWORD` | SMTP password for email sending |
| `DATABASE_URL` / `POSTGRES_*` | Database connection parameters |
| `DB_POOL_*` | Connection pool settings |
| `HOST` / `PORT` | Server bind address/port |
| `LOG_LEVEL` | Logging level |
| `NO_STRICT_CONFIG` | If `true`, skip hard exit on config validation failure |
| `MONITORING_*` | Monitoring and alert thresholds |

---

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success (or pre-flight checks passed) |
| 1 | General failure (config invalid, migration failed, self-test failed, etc.) |

---

## Examples

```bash
# Quick start
python main.py

# Validate a new config file
python main.py pre-flight --config config/production.yaml

# CI/CD pipeline check
python main.py pre-flight --config config/ci.yaml || exit 1

# Run self-tests before deployment
python main.py self-test

# Generate default config
python main.py create-config

# Apply migrations
python main.py migrate-db

# Rotate secrets in production config
cp config/production.yaml config/production.yaml.bak
python main.py rotate-secrets --config config/production.yaml

# Migrate message encryption keys
python main.py migrate-kek --kek-keyring message_keyring.json \
    --kek-old-env PLEXICHAT_SYSTEM_KEY \
    --kek-new-env PLEXICHAT_MESSAGE_KEY \
    --kek-dry-run
```
