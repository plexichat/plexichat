# Core modules

Business logic for the Plexichat server. Each core package owns its data models, validation, and storage, and is initialized once at startup via `src.server.initializer`.

## Structure

Each module follows a consistent layout:

```
module/
  __init__.py       # Initialization and public exports
  manager.py        # Business logic
  models.py         # Dataclasses
  schema.py         # Validation schemas
  exceptions.py     # Module-specific exceptions
  README.md         # Module documentation
```

## Modules

### Authentication (`auth/`)

Handles authentication and authorization:

- Registration and login.
- Password hashing with Argon2.
- Token and session management.
- Two-factor authentication (TOTP).
- Permission checks.

### Database (`database/`)

Provides the storage abstraction for SQLite and PostgreSQL:

- Connection management.
- Query execution and transactions.
- Caching (Redis/Valkey and in-memory).

### Messaging (`messaging/`)

Manages messages and conversations:

- Message create, read, update, delete.
- Conversation membership.
- Attachment handling and encryption.
- Read receipts.

### Servers (`servers/`)

Manages guilds and channels:

- Server create, read, update, delete.
- Channel create, read, update, delete.
- Member, role, and permission management.
- Invites and bans.

### Presence (`presence/`)

Tracks user presence:

- Online, offline, idle, and do-not-disturb status.
- Custom status and activity.
- Typing indicators.
- Visibility rules for friends and shared servers.

### Relationships (`relationships/`)

Manages friends and blocks:

- Friend requests and friend lists.
- Block lists.
- Relationship status resolution.

### Reactions (`reactions/`)

Handles message reactions:

- Add and remove reactions.
- Reaction counts.
- Per-user reaction tracking.

### Webhooks (`webhooks/`)

Integrates webhooks:

- Webhook create, read, update, delete.
- Execution and token validation.

### Media (`media/`)

Handles file uploads:

- Local or S3 storage.
- Image processing and video metadata extraction.
- Content deduplication.

### Events (`events/`)

Defines the real-time event system:

- `EventType` values including `RATCHET_UPDATE` and `ARTIFACT_*` (`ARTIFACT_CREATE`, `ARTIFACT_UPDATE`, `ARTIFACT_DELETE`, `ARTIFACT_OP`) and PlexiSum types (`WORKBOOK_UPDATE`, `SUM_OPERATION`, `SUM_PRESENCE`).
- `GatewayIntent` flags including `ARTIFACTS` (`1 << 20`) and `SUMS` (`1 << 21`). Both are in `GatewayIntent.default_intents()` and `GatewayIntent.all_intents()` (see `src/core/events/types.py`).
- Event routing and intent filtering (see `src/api/websocket/intents.py`).
- Payload generation and dispatch queue.

### Features (`features/`)

Manages entitlements:

- User tiers and feature flags.
- Badges.
- Rate-limit multipliers.

### Settings (`settings/`)

Syncs user settings:

- Key-value storage.
- Cloud sync.

### Additional modules

| Module | Description |
|--------|-------------|
| `admin/` | Administrative operations |
| `applications/` | Bot applications and OAuth |
| `artifacts/` | Collaborative artifacts (whiteboards, live editors) and the `artifact_ops` log. Real-time fabric uses opcodes `60`–`63`. |
| `automod/` | Auto-moderation rules and actions |
| `avatars/` | Avatar storage for users and servers |
| `embeds/` | Rich embed handling |
| `notifications/` | Push notifications |
| `polls/` | Message polls and expiry sweep |
| `ratelimit/` | Rate-limiting core |
| `search/` | Message search |
| `soundboard/` | Soundboard sounds per server |
| `stickers/` | Sticker management |
| `suite/stash/` | PlexiStash file stash |
| `suite/sum/` | PlexiSum workbooks. Real-time fabric uses opcodes `64`–`67` and intent `SUMS` (`1 << 21`). |
| `telemetry/` | Client usage telemetry |
| `threads/` | Thread channels |
| `tls/` | TLS configuration |
| `voice/` | Voice channel signaling (opcodes `4`, `20`–`26`) |

## Real-time gateway

The WebSocket gateway at `/gateway` (not under `/api/v1`) exposes the intents and opcodes defined in this package:

- Opcodes: `RATCHET_UPDATE` (`50`), `ARTIFACT_SUBSCRIBE` (`60`), `ARTIFACT_UNSUBSCRIBE` (`61`), `ARTIFACT_OP` (`62`), `ARTIFACT_SYNC` (`63`), `SUM_SUBSCRIBE` (`64`), `SUM_UNSUBSCRIBE` (`65`), `SUM_OP` (`66`), `SUM_SYNC` (`67`). See `src/api/websocket/opcodes.py`.
- Intents: `ARTIFACTS` (`1 << 20`) gates artifact events; `SUMS` (`1 << 21`) gates PlexiSum events.
- REST probes: `GET /api/v1/health` and `GET /api/v1/ready` (`GET /health` proxies to the former). See `src/api/routes/health.py`.

## Module initialization

Initialize modules from the server lifecycle:

```python
from src.core import auth, messaging, servers

auth.setup(db, config)
messaging.setup(db, config)
servers.setup(db, config)
```

Initialize in the order `src.server.initializer.initialize_modules` defines, then create the app with `src.bootstrap.app.create_application`.

## Access modules

After initialization, use the API getters:

```python
import src.api as api

auth = api.get_auth()
messaging = api.get_messaging()
servers = api.get_servers()
presence = api.get_presence()
```

## Related

- `src/api/websocket/README.md` — Gateway endpoint, opcodes, and intents.
- `src/api/websocket/handlers/README.md` — Per-opcode handler details.
- `src/bootstrap/README.md` — Startup helpers that wire these modules into FastAPI.
- `src/cli/README.md` — CLI that runs the startup sequence.
