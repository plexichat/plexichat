# Plexichat server

[![dev pipeline status](https://gitlab.plexichat.com/plexichat/plexichat/badges/dev/pipeline.svg)](https://gitlab.plexichat.com/plexichat/plexichat/-/pipelines)
[![master pipeline status](https://gitlab.plexichat.com/plexichat/plexichat/badges/master/pipeline.svg)](https://gitlab.plexichat.com/plexichat/plexichat/-/pipelines)
[![tag](https://img.shields.io/gitlab/v/tag/plexichat%2Fplexichat?gitlab_url=https://gitlab.plexichat.com)](https://gitlab.plexichat.com/plexichat/plexichat/-/tags)
[![license](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue)](https://gitlab.plexichat.com/plexichat/plexichat/-/blob/master/LICENSE)

Plexichat is a real-time messaging platform with a REST API and WebSocket gateway.

## Deploy quickly

Use the standalone deploy scripts to provision secrets, config, and the Docker Compose stack. You do not need to clone the repository.

**Linux or macOS:**

```bash
curl -sSL https://plexichat.com/deploy.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://plexichat.com/deploy.ps1 | iex
```

The scripts are interactive by default. Use `--non-interactive` to accept defaults.

## Run from source (development only)

```bash
git clone https://gitlab.plexichat.com/plexichat/plexichat.git
cd plexichat
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements.txt
python main.py
```

Requirements:

* Python `3.14.8` (see `Dockerfile:1`)
* PostgreSQL `18-alpine` and Valkey `9.1.1-alpine` when you use Docker Compose (see `docker-compose.yml:25`, `docker-compose.yml:43`)
* Node `20+` for the client build

Useful commands:

| Command | Purpose |
|---|---|
| `python main.py` | Start the server on `http://127.0.0.1:8000` |
| `python main.py pre-flight` | Validate config without starting |
| `python main.py --help` | List CLI options |

The server stores data in `~/.plexichat/` and exposes:

* API at `http://127.0.0.1:8000/api/v1` (`api.api_prefix` in `src/config_defaults.py:130`)
* WebSocket gateway at `ws://127.0.0.1:8000/gateway` (`src/api/websocket/gateway.py` (`gateway_endpoint`))
* Health probes at `http://127.0.0.1:8000/api/v1/ready` and `http://127.0.0.1:8000/api/v1/health` (`src/api/routes/health.py:18`, `src/api/routes/health.py:47`)
* Interactive docs at `http://127.0.0.1:8000/docs`

## Configure

Plexichat loads configuration in this order (later sources override earlier ones):

1. `config/config.yaml` in the repository
2. `~/.plexichat/config/config.yaml` (user home)
3. Environment variables such as `PLEXICHAT_CONFIG`, `PLEXICHAT_SYSTEM_KEY`, `POSTGRES_PASSWORD`, and `VALKEY_PASSWORD`

If you set `PLEXICHAT_CONFIG`, it points directly to the file you want to use (see `docker-compose.yml:167`).

### Core settings

| Key | Default | Description |
|---|---|---|
| `server.host` | `127.0.0.1` | Bind address (`src/config_defaults.py:70`) |
| `server.port` | `8000` | HTTP port |
| `api.api_prefix` | `/api/v1` | Prefix for all REST routes (`src/config_defaults.py:130`) |
| `tls.enabled` | `false` | Enable TLS; cert paths default to `~/.plexichat/certs/` (`src/config_defaults.py:621`) |
| `voice.sfu_backend` | `aiortc` | SFU backend: `aiortc`, `mediasoup`, or `janus` (`src/config_defaults.py:396`) |
| `database.type` | `sqlite` | `sqlite` for development, `postgres` for production |
| `valkey.enabled` | `false` | Set `true` when you run multiple workers |

### Rate limits

Global defaults live in `src/config_defaults.py:270`:

| Scope | Requests per window | Window | Burst | Notes |
|---|---|---|---|---|
| Global | 25000 | 60 s | 5000 | Applies to all requests |
| Per user | 10000 | 60 s | 2500 | Hourly `300000`, daily `5000000` |
| Per IP | 5000 | 60 s | 1500 | Hourly `150000`, daily `1000000` |
| Static HTML | 300 | 60 s | 100 | |
| Static assets | 5000 | 60 s | 1000 | |

Tune these values in your `config.yaml` under `rate_limiting`.

### TLS

Set `tls.enabled: true` and provide `tls.cert_path` and `tls.key_path`, or let the `cert-init` container generate a self-signed cert for development.

## Authenticate

### OAuth 2.0 with PKCE

Plexichat supports OAuth with PKCE. Only `S256` is accepted (`src/api/routes/auth/oauth.py:202`):

1. Call `GET /api/v1/auth/oauth/{provider}/login?redirect_uri=...&code_challenge=...&code_challenge_method=S256`.
2. You must include a `code_challenge` (43–128 characters, `A-Za-z0-9-._~`). Requests without it return `400`.
3. Complete the flow at `POST /api/v1/auth/oauth/{provider}/callback` with the matching `code_verifier`.

Supported providers are `google`, `github`, and `microsoft`. The `redirect_uri` must be on the allow-list.

### Presence

* Update your presence: `PUT /api/v1/users/@me/presence`
* Get another user's presence: `GET /api/v1/users/{user_id}/presence`

You can read a user's presence only if you are that user, a friend, or a member of a shared server. Other requests return `404` (`src/api/routes/presence.py:379`).

### RBAC and scopes

* Wildcard permissions such as `*` are rejected (`src/core/servers/permission_utils.py:28`).
* Permission scopes are defined in `src/core/admin/permissions.py:42` and enforced by `require_admin_permission` (`src/api/routes/admin/utils.py:164`).

## Use the API

### Route groups

The API router mounts these groups under `/api/v1` (`src/api/app.py:515`):

| Prefix | Description |
|---|---|
| `/auth` | Register, login, 2FA, OAuth, sessions |
| `/users` | Current user, profiles, settings |
| `/users/@me/settings` | Single setting and bulk update (`PUT /users/@me/settings/bulk` at `src/api/routes/settings.py:147`) |
| `/servers` | Servers, channels, members, roles, invites |
| `/channels` | Channel messages, bulk delete, read receipts, typing |
| `/relationships` | Friends, blocks |
| `/presence` | Presence |
| `/webhooks` | Webhook CRUD and `POST /webhooks/{id}/{token}` execution (`src/api/routes/webhooks.py:455`) |
| `/media` | Uploads, chunked uploads, `GET /media/proxy?url=...` (`src/api/routes/media.py:465`), hash reports |
| `/avatars` | User and server avatars |
| `/search` | Message search |
| `/polls`, `/reactions`, `/threads`, `/voice`, `/emojis`, `/stickers`, `/soundboard` | Feature routes |
| `/federation`, `/Join` | Federation |
| `/admin` | Admin UI and API |
| `/docs/api` | Project docs portal |

### Key request details

* Create a channel: send `channel_type`, not `type` (`src/api/routes/servers/channels.py:134` uses `ChannelType`). Example:

  ```json
  {
    "name": "general",
    "channel_type": "text"
  }
  ```

* Message read state: `read_by` is a list of `ReaderInfo` objects (`src/api/schemas/messages.py:94`), not a count.
* Bulk delete: `POST /api/v1/channels/{channel_id}/messages/bulk-delete` takes `{"message_ids": [...]}` (max 100) and emits `MESSAGE_DELETE_BULK` (`src/api/routes/messages/messages_crud/delete.py:218`).
* Media proxy: `GET /api/v1/media/proxy?url=https://...` fetches external images server-side with SSRF protection and caching.
* Webhooks: require `X-Plexichat-Signature` and `X-Plexichat-Timestamp` when `require_signed_deliveries` is true.

### WebSocket gateway

Connect to `/gateway`:

```js
const ws = new WebSocket("ws://127.0.0.1:8000/gateway");
```

* Heartbeat interval: `45000` ms (`src/config_defaults.py:162`)
* Gateway opcodes: `50` (`RATCHET_UPDATE`), `60`–`67` (artifacts and PlexiSum: `ARTIFACT_SUBSCRIBE`, `ARTIFACT_UNSUBSCRIBE`, `ARTIFACT_OP`, `ARTIFACT_SYNC`, `SUM_SUBSCRIBE`, `SUM_UNSUBSCRIBE`, `SUM_OP`, `SUM_SYNC`), and `68` (`ARTIFACT_OP_REJECTED`) — see `src/api/websocket/opcodes.py:40`
* Intents: `20` (`ARTIFACTS`) and `21` (`SUMS`) — see `src/core/events/types.py:120`

Authenticate with `op 2 IDENTIFY` and send `op 1 HEARTBEAT`.

## Licensing

Plexichat requires a license for commercial use. Without one, premium features are disabled.

License lookup order:

1. `PLEXICHAT_LICENSE` env var pointing to a file
2. `PLEXICHAT_LICENSE` env var as base64-encoded JSON
3. `~/.plexichat/config/license`
4. `~/.plexichat/config/license.json`

Apply a license in memory with `POST /api/v1/admin/license/apply` or persist it to disk and call `POST /api/v1/admin/license/check`.

Contact `sales@plexichat.com` or visit `https://plexichat.com`.

## Project structure

```
plexichat/
+-- main.py                 # Entry point
+-- requirements.in         # Direct deps (source of truth)
+-- requirements.txt        # Pinned hashes (generated)
+-- config/                 # Default config templates
+-- docs/                   # Narrative docs (portal at /docs/api)
+-- src/
|   +-- api/routes/         # REST endpoints
|   +-- api/websocket/      # Gateway
|   +-- core/               # Domain logic
|   +-- utils/              # Helpers
+-- docker/                 # Compose assets
```

## Back up

Losing `~/.plexichat` key material makes encrypted data unrecoverable. The Compose `backup` service takes daily restic snapshots (see `docker/README.md`):

```bash
docker exec plexichat-backup-1 /scripts/backup.sh list
docker exec plexichat-backup-1 /scripts/backup.sh restore-db latest
```

For production, set `PLEXICHAT_SYSTEM_KEY` instead of relying on `.machine_key`. See `docs/security.md`.

## Test

Test frameworks are test-only dependencies, so install the dev lock (it
includes the runtime tree):

```bash
pip install --require-hashes -r requirements-dev.txt
pytest src/tests
pytest src/tests -m "slow"          # Timing-sensitive suites
pytest src/tests -m "postgres"      # Requires Postgres
```

Client chat-runtime tests:

```bash
node --test tests/unit/channel-ratchet.test.mjs tests/unit/websocket-dedup.dom.test.mjs
```

For details, see `src/tests/README.md` and `docs/README.md`.

## Version

Current version: `a.1.0-153` (Alpha), defined in `src/server/lifecycle.py:16`.

`src.config_defaults.default_version()` reads that constant lazily, so a
freshly generated config always advertises the build the process is running.

Releases are cut by tagging a `dev` commit with this same string; the tag
pipeline merges `dev` into `master`, and `master` then marks the merge as
`<version>-build<pipeline id>` (`a.1.0-build<pipeline id>` today, since the tag
is derived from this constant). See
`docs/deployment/versioning.md#release-flow`.
