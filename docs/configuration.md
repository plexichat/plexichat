# Configuration Overview

This page covers the backend configuration surface at a high level without prescribing deployment-specific values.

## Config Discovery

At startup the backend looks for configuration in this order:

1. `plexichat/config/config.yaml`
2. `~/.plexichat/config/config.yaml`
3. built-in defaults from the application

If no file is present, the server runs with defaults and fills in the rest from code.

## Public Runtime Configuration

Not every configuration value should be exposed to clients. For client-safe discovery, use:

- `GET {{BASE_URL}}/capabilities`
- `GET {{BASE_URL}}/version`
- `GET {{BASE_URL}}/status`

These endpoints expose public constants and server state without revealing private secrets.

## Major Configuration Areas

| Area | Purpose |
|------|---------|
| `api` | API prefix, generated docs paths, CORS, and OpenAPI settings |
| `server` | bind address, port, and process behavior |
| `authentication` | account policy, sessions, 2FA, and login protections |
| `database` | persistence backend and pooling behavior |
| `rate_limiting` | global, user, IP, and route-level request limits |
| `media` | upload rules, attachment processing, and compression |
| `search` | search backends and public discovery behavior |
| `docs` | custom docs portal path, theme, and feature flags |
| `voice` | ICE server exposure and voice signaling configuration |
| `features` | user tiers, badges, and gated capabilities |

## Safe Practices

- keep secrets out of repository-tracked config files
- validate changes in a non-production environment first
- prefer capability and health endpoints for runtime verification
- avoid depending on undocumented defaults when generated OpenAPI already describes a route

## Related Pages

- [Deployment](deployment.md)
- [Security](security.md)
- [Performance](performance.md)