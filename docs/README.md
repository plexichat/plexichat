# Plexichat documentation

This directory contains the narrative docs that the backend serves at `/docs/api`. For exact request and response schemas, use the OpenAPI docs at `/openapi.json` or `http://127.0.0.1:8000/docs`.

## Get started

| Document | What you learn |
|---|---|
| `index.md` | Portal home and navigation |
| `getting-started.md` | First request and capability discovery |
| `configuration.md` | Configuration areas and file precedence |
| `deployment.md` | Docker Compose and manual deployment |
| `rate-limits.md` | Rate-limit tiers (global 25000, user 10000, IP 5000 per 60 s — see `src/config_defaults.py:273`) |
| `features.md` | Feature map and license gates |
| `security.md` | Public security guidance, SSRF and media proxy details |
| `api/index.md` | REST route-group overview (all groups under `/api/v1`, see `src/config_defaults.py:131`) |
| `websocket/index.md` | Gateway overview (`/gateway` at `src/api/websocket/gateway.py:261`) |

## Reference pages

The `api/` and `websocket/` subdirectories provide per-group docs:

* API groups: `auth`, `users`, `servers`, `channels`, `messages`, `relationships`, `presence`, `reactions`, `webhooks`, `media`, `search`, `polls`, `voice`, `reports`, `feedback`, `telemetry`, `system`, `artifacts`, `federation`, `suite`
* Gateway: opcodes `50` and `60`–`67`, intents `20` and `21` (see `src/api/websocket/opcodes.py:50` and `src/core/events/types.py:120`)
* Health: `GET /api/v1/ready` (readiness) and `GET /api/v1/health` (detailed) — see `src/api/routes/health.py:18`

If you need the live route list, inspect `src/api/routes/__init__.py:56`, which registers every router under `/api/v1`.

## Key API conventions

* Base prefix is `/api/v1` (`api.api_prefix` in `src/config_defaults.py:131`). Do not use `/api` alone.
* Gateway path is `/gateway` (`src/api/websocket/gateway.py:261`), not `/ws`.
* Channel creation uses `channel_type` (`src/api/routes/servers/channels.py:85`), for example `{"name": "general", "channel_type": "text"}`.
* `read_by` on messages is a list of `ReaderInfo` objects (`src/api/schemas/messages.py:94`).
* OAuth requires PKCE `S256` with a `code_challenge` (`src/api/routes/auth/oauth.py:122`).
* Presence reads outside your friends and shared servers return `404` (`src/api/routes/presence.py:379`).
* Bulk delete is `POST /api/v1/channels/{channel_id}/messages/bulk-delete` (max 100 IDs, see `src/api/routes/messages/messages_crud/delete.py:218`).
* Media proxy is `GET /api/v1/media/proxy?url=...` (`src/api/routes/media.py:465`).
* Webhooks live under `/api/v1/webhooks`; execution is `POST /api/v1/webhooks/{webhook_id}/{token}` (`src/api/routes/webhooks.py:455`).
* Settings bulk update is `PUT /api/v1/users/@me/settings/bulk` (`src/api/routes/settings.py:147`).
* Rate limits default to global `25000`, user `10000`, and IP `5000` per `60` s (`src/config_defaults.py:273`).

## Configuration discovery

Plexichat loads config in this order:

1. `config/config.yaml` in the checkout
2. `~/.plexichat/config/config.yaml`
3. File pointed to by `PLEXICHAT_CONFIG` (see `docker-compose.yml:167`)
4. Environment variable overrides (for example `POSTGRES_PASSWORD`, `VALKEY_PASSWORD`, `PLEXICHAT_SYSTEM_KEY`)

Values from later sources override earlier ones.

## Documentation rules

Follow these rules when you edit files in this directory:

* Keep examples runnable against the current codebase.
* Use runtime placeholders such as `{{BASE_URL}}` (for example `http://127.0.0.1:8000/api/v1`) and `{{WEBSOCKET_URL}}` (`ws://127.0.0.1:8000/gateway`).
* Do not include private infrastructure details, secrets, or environment-specific runbooks.
* Use code font for paths, code, and config keys (for example `` `src/config_defaults.py` ``, `` `server.host` ``).
* Prefer tables for options and numbered steps for procedures.
* When narrative docs would duplicate schema details, link to the OpenAPI docs.

## Related docs

* Main readme: `../README.md`
* Docker stack: `../docker/README.md`
* Compose file: `../docker-compose.yml`
* Default config: `../src/config_defaults.py`
* API router: `../src/api/routes/__init__.py`
* Gateway: `../src/api/websocket/gateway.py`
