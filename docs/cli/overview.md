---
sidebar_position: 1
title: CLI Reference
description: Command-line interface reference for the Plexichat server
---

# Plexichat CLI reference

Manage the Plexichat server through a single entry point: `python main.py`. Every operation is a subcommand.

## Usage

```bash
python main.py [global-options] <subcommand> [subcommand-options]
```

To see all subcommands, run:

```bash
python main.py --help
```

To see help for one subcommand, run:

```bash
python main.py <subcommand> --help
```

Running `python main.py` without a subcommand is equivalent to `python main.py start`.

## Global options

Place these options before the subcommand. They apply to all subcommands.

| Option | Description |
|---|---|
| `--config PATH` | Path to a custom YAML configuration file. If you omit this flag, the server auto-detects `~/.plexichat/config/config.yaml` first, then `./config/config.yaml` (`src/server/config_loader.py:440`). |
| `--host HOST` | Override the server bind address. Default `127.0.0.1` from `server.host` (`src/config_defaults.py:70`). |
| `--port PORT` | Override the server port. Default `8000` from `server.port` (`src/config_defaults.py:70`). |

Environment variable alternatives:

| Variable | Overrides |
|---|---|
| `PLEXICHAT_CONFIG` | Config file path (alternative to `--config`) |
| `HOST` | Bind address (`server.host`) |
| `PORT` | Port (`server.port`) |

If you misspell a subcommand, the CLI suggests the closest match:

```bash
python main.py staert
# error: argument command: invalid choice: 'staert'
# Did you mean 'start'?
```

## Subcommands

### `start`

Start the API server. This is the default.

```bash
python main.py start
python main.py start --host 0.0.0.0 --port 8080
python main.py start --config production.yaml
```

The server does the following, in order:

1. Create data directories under `~/.plexichat/` (`data`, `logs`, `media`, `temp`, `config`).
2. Load configuration from `--config`, `PLEXICHAT_CONFIG`, or the auto-detected path.
3. Set up logging and security key checks.
4. Validate configuration types.
5. Initialize utilities, encryption, licensing, and keyrings.
6. Initialize the database (with migrations), auth, messaging, and all core modules.
7. Create the FastAPI application with all routes, including the WebSocket gateway at `/gateway` (`src/api/websocket/gateway.py` (`gateway_endpoint`)) and the API prefix `/api/v1` (`src/config_defaults.py:130`).
8. Listen on the configured host and port.

### `pre-flight`

Validate the entire configuration and module initialization without binding to a port. Use this in CI/CD pipelines or before a restart.

```bash
python main.py pre-flight
python main.py pre-flight --config staging.yaml
```

The check runs the full startup sequence:

1. Directory creation.
2. Configuration loading and validation.
3. Security key checks (warns about weak or default keys).
4. Config type structure validation.
5. Encryption, licensing, and keyring setup.
6. Database connection and migration.
7. All module initializations (auth, messaging, servers, and others).
8. FastAPI application creation.

On success, the command prints a confirmation and exits with `0`. On failure, it exits with `1`. No port is bound.

### `self-test`

Start the server on `127.0.0.1`, run the automated API self-test suite, then shut down.

```bash
python main.py self-test
python main.py self-test --unlicensed
python main.py self-test --purge-residue
```

The suite starts uvicorn in a background thread, runs every test in the self-test runner, and exits with `0` on success or `1` on failure.

| Flag | Description |
|---|---|
| `--unlicensed` | Run in unlicensed mode: exclude license-gated endpoints (for example, `/api/v1/admin/plexijoin/*`) that return `403` without a valid license. The flag is opt-in and the server never infers it from local license state (`src/cli/main.py:236`). Without it those routes stay in the run; on a free-tier install a `403` from a license-gated route is recorded as an **expected denial** (the feature gate working, not a failure), while a licensed install must still answer `2xx`. |
| `--purge-residue` | Run only the account-purge residue check and exit: purge a throwaway account through the admin endpoint, then read the database and storage back to prove its server, channel, conversation, rows and stored upload went with it. Opt-in and never part of the default suite, because the check deletes an account. See [Account-purge residue check](../admin/purge-residue-check.md). |

### `create-config`

Generate a default `config.yaml` with all settings at their factory defaults.

```bash
python main.py create-config
```

The command writes to the first writable location in the search order:

1. `~/.plexichat/config/config.yaml` (preferred)
2. `<project_root>/config/config.yaml` (fallback)

If a file already exists at the chosen location, the command does nothing and reports the existing path (`src/cli/main.py:532`).

### `version`

Print the current server version and exit.

```bash
python main.py version
# Output: Plexichat Server a.1.0-167
```

### `rotate-secrets`

Regenerate the `applications.webhook_signature_secret` value in the config file with a new cryptographically random value.

```bash
python main.py rotate-secrets
python main.py rotate-secrets --config config/production.yaml
```

The command updates the file in place. Back up the file before you run this in production. The new value is `secrets.token_hex(32)` (`src/cli/main.py:562`).

### `migrate-db`

Connect to the database, apply all pending migrations, print a summary, and exit.

```bash
python main.py migrate-db
python main.py migrate-db --dry-run
```

| Flag | Description |
|---|---|
| `--dry-run` | Simulate migration without writing changes. Default `false` (`src/cli/main.py:300`). |

Exits with `0` on success or `1` if any migration fails.

### `migrate-kek`

Manage Key Encryption Key (KEK) migrations. Validate, migrate, or rollback keyrings.

```bash
python main.py migrate-kek --kek-validate --kek-all
python main.py migrate-kek --kek-keyring message_keyring.json \
    --kek-old-env PLEXICHAT_SYSTEM_KEY \
    --kek-new-env PLEXICHAT_MESSAGE_KEY
python main.py migrate-kek --kek-all --kek-new-env PLEXICHAT_SYSTEM_KEY
python main.py migrate-kek --kek-rollback --kek-keyring message_keyring.json
python main.py migrate-kek --kek-keyring message_keyring.json \
    --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY \
    --kek-dry-run
```

**KEK options**

| Option | Description |
|---|---|
| `--kek-keyring FILE` | Specific keyring to migrate (for example, `message_keyring.json`). |
| `--kek-old-env VAR` | Environment variable name for the old KEK (for example, `PLEXICHAT_SYSTEM_KEY`). |
| `--kek-new-env VAR` | Environment variable name for the new KEK (for example, `PLEXICHAT_MESSAGE_KEY`). |
| `--kek-all` | Migrate all keyrings to the new KEK. Requires `--kek-new-env`. |
| `--kek-validate` | Validate keyring integrity without making changes. |
| `--kek-rollback` | Roll back a keyring from its backup file. |
| `--kek-force` | Force migration even if validation fails. |
| `--kek-dry-run` | Simulate migration without writing changes. |

## Configuration keys

The CLI and `config.yaml` use the keys defined in `src/config_defaults.py`. Common mistakes to avoid:

| Wrong key | Correct key | Notes |
|---|---|---|
| `api.host`, `api.port`, `api.workers` | `server.host`, `server.port`, `server.reload` | Server binding is under `server` (`src/config_defaults.py:70`) |
| `api.prefix` | `api_prefix` | Nested under `api` as `api.api_prefix` (`src/config_defaults.py:130`) |
| Nested `cors.*` object | Flat `cors_*` keys | For example, `api.cors_origins`, `api.cors_allow_methods` (`src/config_defaults.py:132`) |
| `api.ssl` | `tls` | TLS is a top-level `tls` block (`src/config_defaults.py:618`) |
| `mediasoup` assumed default | `aiortc` | Default SFU is `aiortc`; `mediasoup` and `janus` are opt-in (`src/config_defaults.py:396`) |

Before you change the server config, open `src/config_defaults.py` and confirm the key name.

## Environment variables

In addition to the global options, these variables affect server behavior:

| Variable | Purpose |
|---|---|
| `PLEXICHAT_CONFIG` | Config file path (alternative to `--config`). |
| `PLEXICHAT_SYSTEM_KEY` | Root system encryption key. |
| `PLEXICHAT_MESSAGE_KEY` | Message encryption key. |
| `PLEXICHAT_SMTP_PASSWORD` | SMTP password for email. |
| `DATABASE_URL` or `POSTGRES_*` | Database connection. |
| `DB_POOL_*` | Connection pool settings. |
| `HOST` or `PORT` | Server bind address and port (override `server.host` and `server.port`). |
| `LOG_LEVEL` | Logging level. |
| `NO_STRICT_CONFIG` | When `true`, skip hard exit on config validation failure. |
| `MONITORING_*` | Monitoring and alert thresholds. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, or pre-flight checks passed. |
| `1` | General failure: invalid config, migration failure, self-test failure, or port already in use. |

## Examples

```bash
# Start with defaults
python main.py

# Use a custom config
python main.py --config config/production.yaml

# Validate a new config without starting the server
python main.py pre-flight --config config/production.yaml

# CI/CD gate: fail the pipeline if pre-flight fails
python main.py pre-flight --config config/ci.yaml || exit 1

# Run self-tests before deployment
python main.py self-test
python main.py self-test --unlicensed

# Generate a default config if none exists
python main.py create-config

# Apply pending migrations
python main.py migrate-db
python main.py migrate-db --dry-run

# Rotate the webhook signature secret
cp config/production.yaml config/production.yaml.bak
python main.py rotate-secrets --config config/production.yaml

# Migrate message encryption keys (dry run first)
python main.py migrate-kek --kek-keyring message_keyring.json \
    --kek-old-env PLEXICHAT_SYSTEM_KEY \
    --kek-new-env PLEXICHAT_MESSAGE_KEY \
    --kek-dry-run
```

For subcommand-specific flags, run `python main.py <subcommand> --help`.
