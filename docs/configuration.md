# Configuration Overview

This page provides a high-level overview of Plexichat configuration. For detailed configuration guidance for each module, see the module-specific guides below.

## Quick Start

For a complete reference of all configuration options with default values, see the [Default Configuration Reference](default-config.md). Copy the complete config file from that document as your starting point.

## Environment Variable Interpolation

Plexichat supports environment variable interpolation in configuration files using the `${VAR_NAME}` syntax.

### Syntax

- **Required variable**: `${VAR_NAME}` - Will raise an error if the environment variable is not set
- **Optional variable with default**: `${VAR_NAME:-default_value}` - Uses the default if the variable is not set

### Example

```yaml
database:
  postgres:
    host: "${POSTGRES_HOST:-localhost}"  # Optional, defaults to localhost
    password: "${POSTGRES_PASSWORD}"       # Required, will error if not set
```

### Benefits

- **Security**: Keep secrets out of version control
- **Flexibility**: Use different configurations for different environments (dev, staging, production)
- **Docker-friendly**: Works seamlessly with container orchestration systems
- **12-factor app compliance**: Follows best practices for configuration management

### Startup Behavior

- **Missing required variable**: Application will fail to start with a clear error message indicating which variable is missing. To avoid this, always provide a default using the `:-` syntax (e.g., `${POSTGRES_PASSWORD:-}` for optional credentials).
- **Using default**: Application logs when it uses a default value for an optional variable
- **Resolved variables**: Application logs which environment variables were successfully resolved (key names only, never values)

### Type Coercion

Environment variable interpolation resolves to strings by default. Type coercion (converting `"5432"` to integer `5432`, or `"true"` to boolean `True`) is **only** applied when:

1. The value contains an interpolation pattern (`${...}`), AND
2. The corresponding default config value provides a type hint (is not a string)

This means:
- Non-interpolated string values like `"localhost"` or `"CHANGE_THIS_SIGNING_KEY"` are always kept as strings
- Interpolated values like `${POSTGRES_PORT:-5432}` will be coerced to match the default's type (int in this case)
- Passwords and secrets (which have empty-string defaults) remain as strings even if they look like numbers

This prevents silent data corruption where values like `"0"` become `False` or numeric passwords become integers.

### Bootstrap Environment Variables

Some environment variables are used before the configuration system is initialized and must be set directly:

- `PLEXICHAT_SYSTEM_KEY`: System encryption key for vault initialization (bootstrap-level security)
- `PLEXICHAT_WORKER_ID`: Worker identifier for distributed deployments
- `PLEXICHAT_DATACENTER_ID`: Datacenter identifier for multi-region deployments

These bootstrap variables bypass the config interpolation system because they are needed during early initialization.

## Config Discovery

At startup the backend looks for configuration in this order:

1. `./config/config.yaml` (project directory)
2. `~/.plexichat/config/config.yaml` (home directory)
3. Built-in defaults from the application

If no file is present, the server runs with defaults and logs a warning. Environment variable interpolation is applied after the configuration file is loaded and merged with defaults.

## Module-Specific Configuration

For detailed configuration guidance for each module, refer to these guides:

- **[Default Configuration Reference](default-config.md)** - Complete reference of all configuration options with default values
- **[Authentication Configuration](deployment/configuration/config-authentication.md)** - Password policies, 2FA, sessions, account deletion, and security settings
- **[Database Configuration](deployment/configuration/config-database.md)** - PostgreSQL/SQLite setup, connection pooling, migrations, and scaling
- **[Email Configuration](deployment/configuration/config-email.md)** - SMTP settings for notifications and verification
- **[Embed Configuration](deployment/configuration/config-embeds.md)** - URL preview and link embed settings
- **[Redis Configuration](deployment/configuration/config-redis.md)** - Caching, session storage, connection pooling, and scaling strategies
- **[Media Configuration](deployment/configuration/config-media.md)** - File uploads, storage backends (local/S3), processing, and security
- **[Voice Configuration](deployment/configuration/config-voice.md)** - WebRTC signaling, SFU backends, STUN/TURN servers, and NAT traversal
- **[WebSocket Configuration](deployment/configuration/config-websocket.md)** - Gateway settings, compression, rate limits, and origin validation
- **[Search Configuration](deployment/configuration/config-search.md)** - Search backends, indexing, server discovery, and result limits
- **[Rate Limiting Configuration](deployment/configuration/config-rate-limiting.md)** - Global, user, IP, bot, and webhook rate limits

## Deployment Guides

For deployment-related documentation:

- **[Getting Started](deployment/getting-started.md)** - Production deployment steps, Docker setup, and initial configuration
- **[Deployment Overview](deployment/overview.md)** - High-level deployment architecture and strategies
- **[Requirements](deployment/requirements.md)** - System requirements for production and development

## Public Runtime Configuration

Not every configuration value should be exposed to clients. For client-safe discovery, use:

- `GET {{BASE_URL}}/capabilities`
- `GET {{BASE_URL}}/version`
- `GET {{BASE_URL}}/status`

These endpoints expose public constants and server state without revealing private secrets.

## Major Configuration Areas

- `authentication` -- account policy, sessions, 2FA, login protections, age gate: [Authentication Configuration](config-authentication.md)
- `database` -- persistence backend and pooling behavior: [Database Configuration](config-database.md)
- `redis` -- caching, session storage, pub/sub: [Redis Configuration](config-redis.md)
- `media` -- upload rules, attachment processing, compression: [Media Configuration](config-media.md)
- `voice` -- ICE servers, SFU backend, voice signaling: [Voice Configuration](config-voice.md)
- `websocket` -- gateway settings, compression, rate limits: [WebSocket Configuration](config-websocket.md)
- `search` -- search backends, public discovery: [Search Configuration](config-search.md)
- `rate_limiting` -- global, user, IP, route-level limits: [Rate Limiting Configuration](config-rate-limiting.md)
- `api` -- API prefix, docs paths, CORS, proxies, TLS: [API & Server Configuration](config-api.md)
- `server` -- bind address, port, workers, reload: [API & Server Configuration](config-api.md)
- `email` -- SMTP setup for verification and password reset: [Email Configuration](config-email.md)
- `embeds` -- embed limits and URL preview: [Embeds & URL Preview Configuration](config-embeds.md)
- `encryption` -- Argon2, AES-GCM, key rotation, snowflake: [Default Configuration Reference](default-config.md)
- `monitoring` -- metrics, alert thresholds, log intervals: [Default Configuration Reference](default-config.md)
- `admin_ui` -- admin panel, OTP, host restriction: [Default Configuration Reference](default-config.md)
- `oauth` -- PKCE, Google/GitHub/Microsoft, state management: [Default Configuration Reference](default-config.md)

## Safe Practices

- Keep secrets out of repository-tracked config files
- Use environment variables for sensitive values (passwords, API keys)
- Validate changes in a non-production environment first
- Prefer capability and health endpoints for runtime verification
- Review module-specific guides before changing critical settings

## Related Pages

- [Deployment Guide](deployment.md) - Installation, security hardening, and operational procedures
- [Security Best Practices](security.md) - Authentication models, session hygiene, and security expectations
- [Performance Guide](performance.md) - Performance characteristics and optimization guidance