# Deployment Guidance

This page intentionally stays high level. It covers the public-facing operational considerations for running Plexichat without embedding environment-specific commands, credentials, or infrastructure details.

## Production Checklist

- configure the server with explicit values instead of relying on defaults
- verify `GET /health`, `GET /api/v1/version`, and `GET /api/v1/status`
- confirm generated docs are reachable at `/docs`
- confirm the custom docs portal is reachable at `/docs/api` when enabled
- validate authentication, uploads, and the WebSocket gateway from a client environment
- make sure secrets and operator-only runbooks stay outside repository docs

## Runtime Endpoints Worth Verifying

| Endpoint | Why it matters |
|----------|----------------|
| `/health` | basic readiness and health signal |
| `/api/v1/version` | current backend version |
| `/api/v1/version/negotiate` | client compatibility check |
| `/api/v1/status` | maintenance and availability state |
| `/docs` | generated OpenAPI surface |
| `/docs/api` | custom narrative docs portal |
| `/gateway` | real-time event delivery |

## Deployment Concerns To Plan For

- persistence and backup strategy
- rate-limit tuning appropriate for your traffic shape
- storage policy for attachments and avatars
- search indexing behavior and discovery visibility
- voice connectivity and ICE/TURN expectations
- access-token policy if the server is intentionally gated
- monitoring, alerting, and incident response outside this public docs set

## Intentionally Omitted

Detailed infrastructure setup, secret material, database migration runbooks, and environment-specific commands should remain in internal operational documentation rather than this served docs surface.
