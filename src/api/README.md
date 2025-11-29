# REST API Module

FastAPI-based REST API for PlexiChat supporting all core module functionality.

## Features

- Application factory pattern with `create_app()`
- Authentication middleware (Bearer and Bot tokens)
- Error handling with consistent JSON format
- Request logging middleware
- CORS configuration
- OpenAPI documentation (auto-generated)
- API versioning (`/api/v1/*`)
- Pydantic v2 request/response validation
- Snowflake IDs as strings in JSON responses

## Setup

```python
from src.core.database import Database
from src.core import auth, messaging, servers, relationships, presence, reactions, embeds, webhooks
from src.api import setup, create_app

# Initialize database
db = Database()
db.connect()

# Initialize core modules
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
relationships.setup(db, auth, servers)
presence.setup(db, auth, relationships, servers)
reactions.setup(db, messaging, servers, relationships)
embeds.setup(db, messaging, servers)
webhooks.setup(db, auth, messaging, servers, embeds)

# Initialize API
setup(
    db=db,
    auth_module=auth,
    messaging_module=messaging,
    servers_module=servers,
    relationships_module=relationships,
    presence_module=presence,
    reactions_module=reactions,
    embeds_module=embeds,
    webhooks_module=webhooks,
)

# Create FastAPI app
app = create_app()

# Run with uvicorn
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=8000)
```

## Endpoints

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | Health check |

### Version & Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/version` | Get server version info |
| POST | `/api/v1/version/negotiate` | Negotiate client/server compatibility |
| GET | `/api/v1/status` | Get server operational status |

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register new user |
| POST | `/api/v1/auth/login` | Login user |
| POST | `/api/v1/auth/2fa` | Complete 2FA |
| POST | `/api/v1/auth/logout` | Logout session |

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/users/@me` | Get current user |
| PATCH | `/api/v1/users/@me` | Update current user |
| GET | `/api/v1/users/{user_id}` | Get user by ID |

### Servers

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/servers` | Get user's servers |
| POST | `/api/v1/servers` | Create server |
| GET | `/api/v1/servers/{id}` | Get server |
| PATCH | `/api/v1/servers/{id}` | Update server |
| DELETE | `/api/v1/servers/{id}` | Delete server |
| GET | `/api/v1/servers/{id}/channels` | Get server channels |

### Channels

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/channels/{id}` | Get channel |
| PATCH | `/api/v1/channels/{id}` | Update channel |
| DELETE | `/api/v1/channels/{id}` | Delete channel |

### Messages

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/channels/{id}/messages` | Get messages |
| POST | `/api/v1/channels/{id}/messages` | Send message |
| GET | `/api/v1/channels/{id}/messages/{msg_id}` | Get message |
| PATCH | `/api/v1/channels/{id}/messages/{msg_id}` | Edit message |
| DELETE | `/api/v1/channels/{id}/messages/{msg_id}` | Delete message |

### Relationships

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/relationships/@me` | Get relationships |
| POST | `/api/v1/relationships` | Send friend request |
| PUT | `/api/v1/relationships/{id}/accept` | Accept request |
| DELETE | `/api/v1/relationships/{id}` | Remove relationship |
| POST | `/api/v1/relationships/block` | Block user |

### Presence

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/api/v1/users/@me/presence` | Update presence |
| GET | `/api/v1/users/{id}/presence` | Get user presence |

### Reactions

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `/api/v1/channels/{id}/messages/{msg_id}/reactions/{emoji}` | Add reaction |
| DELETE | `/api/v1/channels/{id}/messages/{msg_id}/reactions/{emoji}` | Remove reaction |
| GET | `/api/v1/channels/{id}/messages/{msg_id}/reactions` | Get reactions |
| GET | `/api/v1/channels/{id}/messages/{msg_id}/reactions/{emoji}` | Get reaction users |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/webhooks` | Create webhook |
| GET | `/api/v1/webhooks/{id}` | Get webhook |
| DELETE | `/api/v1/webhooks/{id}` | Delete webhook |
| POST | `/api/v1/webhooks/{id}/{token}` | Execute webhook |

## Authentication

Include token in Authorization header:

```
Authorization: Bearer <session_token>
Authorization: Bot <bot_token>
```

## Error Format

All errors follow this format:

```json
{
  "error": {
    "code": 404,
    "message": "Resource not found"
  }
}
```

## Configuration

Settings in `config/config.yaml` under `api`:

```yaml
api:
  title: PlexiChat API
  version: a.1.0-1
  api_prefix: /api/v1
  debug: false
  cors_origins:
    - "*"
  cors_allow_credentials: true
  docs_url: /docs
  redoc_url: /redoc

application:
  name: PlexiChat
  version: a.1.0-1
  environment: development

versioning:
  min_supported_version: a.1.0-1
  update_url: null
```

## Testing

```bash
pytest src/tests/api/ -v
```

## Module Structure

```
src/api/
  __init__.py          # Setup and module exports
  app.py               # FastAPI application factory
  config.py            # API configuration
  dependencies.py      # Dependency injection
  middleware/
    __init__.py
    authentication.py  # Token validation
    error_handling.py  # Exception handlers
    logging.py         # Request logging
  routes/
    __init__.py        # Route registration
    auth.py            # Auth endpoints
    users.py           # User endpoints
    servers.py         # Server endpoints
    channels.py        # Channel endpoints
    messages.py        # Message endpoints
    relationships.py   # Relationship endpoints
    presence.py        # Presence endpoints
    reactions.py       # Reaction endpoints
    webhooks.py        # Webhook endpoints
    health.py          # Health check
  schemas/
    __init__.py
    common.py          # Shared models
    auth.py            # Auth models
    users.py           # User models
    servers.py         # Server models
    channels.py        # Channel models
    messages.py        # Message models
```
