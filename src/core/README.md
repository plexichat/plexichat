# Core Modules

Business logic modules for the Plexichat server.

## Structure

Each module is organized as a package with consistent structure:

```
module/
├── __init__.py      # Module initialization and exports
├── manager.py       # Main business logic
├── models.py        # Data models (dataclasses)
├── schema.py        # Validation schemas
├── exceptions.py    # Module-specific exceptions
└── README.md        # Module documentation
```

## Modules

### Authentication (`auth/`)
User authentication, session management, and authorization.
- Registration and login
- Password hashing (Argon2)
- JWT token generation
- Session management
- Two-factor authentication (TOTP)
- Permission system

### Database (`database/`)
Database abstraction layer supporting SQLite and PostgreSQL.
- Connection management
- Query execution
- Caching (Redis and in-memory)
- Transaction support

### Messaging (`messaging/`)
Message handling and conversation management.
- Message CRUD operations
- Conversation management
- Attachment handling
- Message encryption
- Read receipts

### Servers (`servers/`)
Server/guild management.
- Server CRUD
- Channel management
- Member management
- Role and permission system
- Invite system

### Presence (`presence/`)
User presence and status tracking.
- Online/offline status
- Custom status messages
- Activity tracking
- Visibility rules

### Relationships (`relationships/`)
Friend and block management.
- Friend requests
- Friend list
- Block list
- Relationship status

### Reactions (`reactions/`)
Message reaction system.
- Add/remove reactions
- Reaction counts
- User reaction tracking

### Webhooks (`webhooks/`)
Webhook integration system.
- Webhook CRUD
- Webhook execution
- Token validation

### Media (`media/`)
File upload and processing.
- File storage (local/S3)
- Image processing
- Video metadata extraction
- Content deduplication

### Events (`events/`)
Event system for real-time updates.
- Event types
- Event routing
- Payload generation

### Features (`features/`)
User feature flags and badges.
- User tiers
- Badge management
- Rate limit multipliers

### Settings (`settings/`)
User settings sync.
- Key-value storage
- Cloud sync

### Additional Modules

| Module | Description |
|--------|-------------|
| `admin/` | Admin operations |
| `applications/` | Bot applications and OAuth |
| `automod/` | Auto-moderation |
| `avatars/` | Avatar storage |
| `embeds/` | Rich embed handling |
| `notifications/` | Push notifications |
| `polls/` | Message polls |
| `ratelimit/` | Rate limiting |
| `search/` | Message search |
| `soundboard/` | Soundboard sounds |
| `stickers/` | Sticker management |
| `telemetry/` | Usage telemetry |
| `threads/` | Thread channels |
| `tls/` | TLS configuration |
| `voice/` | Voice channel signaling |

## Module Initialization

Modules are initialized in `main.py`:

```python
from src.core import auth, messaging, servers

auth.setup(db, config)
messaging.setup(db, config)
servers.setup(db, config)
```

## Accessing Modules

Use the API module's getter functions:

```python
import src.api as api

auth = api.get_auth()
messaging = api.get_messaging()
```
