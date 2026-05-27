# API Module

FastAPI application layer for the Plexichat server.

## Structure

```
api/
+-- __init__.py        # Module initialization, getter functions
+-- app.py             # FastAPI application setup
+-- config.py          # API configuration
+-- dependencies.py    # Dependency injection
+-- middleware/        # Request middleware
+-- routes/            # Endpoint handlers
+-- schemas/           # Request/response models
+-- websocket/         # WebSocket gateway
```

## Components

### Application (`app.py`)

Creates and configures the FastAPI application:
- CORS middleware
- Exception handlers
- Route registration
- Startup/shutdown events

### Middleware (`middleware/`)

Request processing middleware:
- `authentication.py` - Token validation and user extraction
- `rate_limiting.py` - Rate limit enforcement
- `error_handling.py` - Global error handling
- `logging.py` - Request logging

### Routes (`routes/`)

API endpoint handlers organized by resource:
- `auth.py` - Authentication endpoints
- `users.py` - User management
- `servers.py` - Server/guild management
- `channels.py` - Channel management
- `messages.py` - Messaging
- `reactions.py` - Message reactions
- `relationships.py` - Friends and blocks
- `presence.py` - User status
- `webhooks.py` - Webhook integration
- `avatars.py` - Avatar management
- `emojis.py` - Custom emoji
- `settings.py` - User settings
- `features.py` - User features/badges (admin)
- `health.py` - Health check
- `version.py` - Version negotiation
- `docs.py` - Documentation server

### Schemas (`schemas/`)

Pydantic models for request validation and response serialization:
- `auth.py` - Auth request/response models
- `users.py` - User models
- `servers.py` - Server models
- `channels.py` - Channel models
- `messages.py` - Message models
- `common.py` - Shared types (SnowflakeID, etc.)

### WebSocket (`websocket/`)

WebSocket gateway implementation:
- `gateway.py` - Main gateway handler
- `connection.py` - Connection management
- `dispatcher.py` - Event dispatching
- `handlers.py` - Opcode handlers
- `session.py` - Session management
- `opcodes.py` - Opcode definitions
- `intents.py` - Gateway intents
- `compression.py` - Payload compression

## Usage

The API module is initialized in `main.py`:

```python
from src.api import create_app

app = create_app()
```

Access core modules through getter functions:

```python
import src.api as api

auth = api.get_auth()
messaging = api.get_messaging()
servers = api.get_servers()
```
