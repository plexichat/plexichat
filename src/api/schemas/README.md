# API Schemas

Pydantic models for request/response validation.

## Schema Categories

- `auth.py` - Login, register, token responses
- `channels.py` - Channel creation requests
- `common.py` - Shared types (SnowflakeID, pagination, errors)
- `messages.py` - Message create/update/response
- `presence.py` - Presence updates and responses
- `reactions.py` - Reaction responses
- `relationships.py` - Friend requests, blocks
- `servers.py` - Server, channel, role, member schemas
- `users.py` - User profile schemas
- `version.py` - Version info schema
- `webhooks.py` - Webhook CRUD schemas

## Usage

```python
from src.api.schemas import (
    LoginRequest,
    MessageCreateRequest,
    ServerResponse,
)
```
