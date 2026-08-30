# Configuration overview

This page describes how Plexichat discovers, merges, and validates configuration. For the complete default file, see the [default configuration reference](default-config.md). For per-module details, see the [module-specific guides](#module-specific-guides).

## Quick start

Copy the complete default file from the [default configuration reference](default-config.md) to your chosen config location and edit it. The key names must match exactly; if you rename a key, the server ignores it.

## Configuration discovery

Plexichat checks for configuration in this order. The first match wins.

1.  `--config /path/to/config.yaml` argument passed to `python main.py`. Use this to point at an explicit file.
2.  `PLEXICHAT_CONFIG` environment variable. Set it to an absolute path when you cannot change the command line (for example, in some container orchestrators).
3.  `~/.plexichat/config/config.yaml` in the home directory. This is the preferred default for production.
4.  `./config/config.yaml` in the project directory. This is convenient for development checkouts.
5.  Built-in defaults from `src/config_defaults.py`.

If no file exists, the server creates one at the home path with the built-in defaults, logs a warning, and continues with those defaults. The auto-detect order (home before project) is implemented in `plexichat/src/server/config_loader.py:setup_config`.

Use `python main.py --config /path/to/config.yaml pre-flight` to validate a file without starting the server.

## Environment variable interpolation

You can reference environment variables inside `config.yaml` with `${VAR}` syntax. The config loader resolves these values after merging the file with defaults.

### Syntax

| Form | Behavior |
| --- | --- |
| `${VAR_NAME}` | Required. The server fails to start with a clear error if the variable is not set. |
| `${VAR_NAME:-default_value}` | Optional. Uses `default_value` when the variable is not set. |

### Example

```yaml
database:
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD}"       # required
valkey:
  host: "${VALKEY_HOST:-localhost}"
  port: "${VALKEY_PORT:-6379}"
```

### Type coercion

Interpolation resolves to a string first. The loader coerces it to the type of the corresponding default value:

- If the default is an integer, `"5432"` becomes `5432`.
- If the default is a boolean, `"true"`, `"1"`, or `"yes"` becomes `true`.
- If the default is a string, the value stays a string. This prevents a password such as `"12345"` from becoming an integer.

Plain values without an interpolation pattern but with a typed default (for example, `port: "6379"` in YAML) are also coerced to the default type so that quoted numbers do not fail validation. Values without a typed default remain strings.

### Startup behavior

- **Missing required variable.** The server fails to start and logs which variable is missing. Use `:-` to make a variable optional.
- **Using a default.** The server logs that it used the fallback value.
- **Resolved variable.** The server logs the variable name, never the value.

### Bootstrap environment variables

Some variables are read before the config system initializes and must be set directly in the environment. They bypass interpolation.

| Variable | Purpose |
| --- | --- |
| `PLEXICHAT_SYSTEM_KEY` | System encryption key for vault initialization |
| `PLEXICHAT_WORKER_ID` | Worker identifier (`0`–`31`) for distributed Snowflake generation |
| `PLEXICHAT_DATACENTER_ID` | Datacenter identifier (`0`–`31`) for distributed Snowflake generation |
| `PLEXICHAT_CONFIG` | Explicit config file path (see [Configuration discovery](#configuration-discovery)) |
| `PLEXICHAT_GITLAB_TOKEN` | Token used by the static-client updater |

## Config file handling

- **Deep merge.** The file you provide is deep-merged over the built-in defaults. You only need to include the keys you want to change. See `plexichat/src/utils/common_utils/utils/config/core.py:ConfigLoader`.
- **Atomic writes.** When the server persists a change with `config.set()`, it writes to a temporary file in the same directory and renames it into place. A crash mid-write leaves the previous file intact.
- **Hot-path cache.** Reads through `config.get()` are cached and invalidated on `set()` or reload.

## Module-specific guides

| Area | Guide |
| --- | --- |
| Authentication (password policy, sessions, 2FA) | [Authentication configuration](deployment/configuration/config-authentication.md) |
| Database (PostgreSQL/SQLite, pooling, migrations) | [Database configuration](deployment/configuration/config-database.md) |
| Email (SMTP, verification) | [Email configuration](deployment/configuration/config-email.md) |
| Embeds and URL previews | [Embed configuration](deployment/configuration/config-embeds.md) |
| Valkey (caching, sessions, pub/sub) | [Valkey configuration](deployment/configuration/config-valkey.md) |
| Media (uploads, storage backends) | [Media configuration](deployment/configuration/config-media.md) |
| Voice (WebRTC signaling, SFU, STUN/TURN) | [Voice configuration](deployment/configuration/config-voice.md) |
| WebSocket gateway (heartbeat, compression, limits) | [WebSocket configuration](deployment/configuration/config-websocket.md) |
| Search (backends, indexing, discovery) | [Search configuration](deployment/configuration/config-search.md) |
| Rate limiting (global, user, IP, webhook) | [Rate limiting configuration](deployment/configuration/config-rate-limiting.md) |
| API and server (CORS, docs paths, TLS) | [API & server configuration](deployment/configuration/config-api.md) |
| Default values for every key | [Default configuration reference](default-config.md) |

## Public runtime configuration

Do not expose private config to clients. For client-safe discovery, use these endpoints:

- `GET {{BASE_URL}}/capabilities`
- `GET {{BASE_URL}}/version`
- `GET {{BASE_URL}}/status`

These endpoints return public constants and server state without revealing secrets.

## Major configuration areas

| Key | Description |
| --- | --- |
| `authentication` | Account policy, sessions, 2FA, and login protections |
| `database` | Persistence backend and connection pooling |
| `valkey` | Caching, session storage, and pub/sub |
| `media` | Upload rules, attachment processing, and storage backends |
| `voice` | ICE servers, SFU backend, and voice signaling |
| `websocket` | Gateway heartbeat, compression, and rate limits |
| `search` | Search backends and server discovery |
| `rate_limiting` | Global, user, IP, and route limits |
| `api` | API prefix, docs paths, CORS, proxies, and TLS |
| `server` | Bind address, port, workers, and reload |
| `email` | SMTP settings for verification and password reset |
| `embeds` | Embed limits and URL preview |
| `encryption` | Argon2, AES-GCM, key rotation, and Snowflake epoch |
| `monitoring` | Metrics, alert thresholds, and log intervals |
| `admin_ui` | Admin panel, OTP, and host restrictions |
| `oauth` | PKCE, provider credentials, and state management |

## Safe practices

- Keep secrets out of version control. Use environment variables or your secret manager.
- Validate changes in a non-production environment first, or run `python main.py pre-flight` before rollout.
- Prefer the capability and health endpoints for runtime verification.
- Review the module-specific guide before you change a critical setting.

## Related docs

- [Default configuration reference](default-config.md)
- [Deployment guide](deployment.md)
- [Security best practices](security.md)
- [Performance guide](performance.md)
