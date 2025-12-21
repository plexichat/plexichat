# Webhooks Module

Webhook system for PlexiChat API supporting automated message posting to channels.

## Features

- Webhook creation with secure token generation
- Webhook management (get, update, delete, regenerate token)
- Webhook execution to send messages
- Username and avatar override per message
- Rich embed support (up to 10 embeds per message)
- Thread posting support
- Permission-based access control
- Token stored as hash for security

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import embeds
from src.core import webhooks

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
embeds.setup(db, messaging, servers)

# Initialize webhooks
webhooks.setup(db, auth, messaging, servers, embeds)
```

## Usage

### Create Webhook

```python
from src.core import webhooks

# Create a webhook for a channel
webhook = webhooks.create_webhook(
    user_id=1,
    channel_id=123,
    name="My Webhook",
    avatar_url="https://example.com/avatar.png"  # Optional
)

# Save the token - only shown on create!
print(f"Token: {webhook.token}")
print(f"URL: {webhook.url}")
```

### Get Webhooks

```python
# Get single webhook
webhook = webhooks.get_webhook(webhook_id=456, user_id=1)

# Get all webhooks for a channel
channel_webhooks = webhooks.get_channel_webhooks(user_id=1, channel_id=123)

# Get all webhooks for a server
server_webhooks = webhooks.get_server_webhooks(user_id=1, server_id=789)
```

### Update Webhook

```python
# Update webhook name and avatar
webhook = webhooks.update_webhook(
    user_id=1,
    webhook_id=456,
    name="New Name",
    avatar_url="https://example.com/new-avatar.png"
)

# Move webhook to different channel
webhook = webhooks.update_webhook(
    user_id=1,
    webhook_id=456,
    channel_id=new_channel_id
)

# Clear avatar
webhook = webhooks.update_webhook(
    user_id=1,
    webhook_id=456,
    avatar_url=""
)
```

### Delete Webhook

```python
webhooks.delete_webhook(user_id=1, webhook_id=456)
```

### Regenerate Token

```python
# Get new token (invalidates old one)
webhook = webhooks.regenerate_token(user_id=1, webhook_id=456)
print(f"New token: {webhook.token}")
```

### Execute Webhook

```python
# Send simple message
webhooks.execute_webhook(
    webhook_id=456,
    token="webhook.456.abc123...",
    content="Hello from webhook!"
)

# Send with username/avatar override
webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",  # Can use just the secret part
    content="Custom message",
    username="Custom Bot",
    avatar_url="https://example.com/custom.png"
)

# Send with embeds
webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",
    embeds=[{
        "title": "Embed Title",
        "description": "Embed description",
        "color": "#FF0000"
    }]
)

# Send to thread
webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",
    content="Thread message",
    thread_id=thread_id
)

# Get message object back
message = webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",
    content="Hello!",
    wait=True
)
print(f"Message ID: {message.id}")
```

### Execute by URL

```python
# Use webhook URL directly
message = webhooks.execute_webhook_by_url(
    webhook_url="/webhooks/456/abc123...",
    content="Hello via URL!",
    wait=True
)
```

## Token Format

Webhook tokens follow the format:
```
webhook.{webhook_id}.{random_secret}
```

Example:
```
webhook.7891234567890123456.a8Kj2mNpQrStUvWxYz...
```

The token is only returned when:
- Creating a new webhook
- Regenerating the token

## Webhook URL Format

```
/webhooks/{webhook_id}/{token}
```

## Limits

| Limit | Default |
|-------|---------|
| Webhooks per channel | 10 |
| Webhooks per server | 50 |
| Webhook name length | 80 characters |
| Username override length | 80 characters |
| Message content length | 2000 characters |
| Embeds per message | 10 |

## Permission Integration

The module requires `webhooks.manage` permission for:
- Creating webhooks
- Viewing webhooks
- Updating webhooks
- Deleting webhooks
- Regenerating tokens

Anyone with the webhook token can execute it (token is the secret).

## Security Features

1. Token Hashing: Only SHA-256 hash stored, not the token itself
2. URL Validation: Avatar URLs must use http/https
3. Content Sanitization: HTML tags stripped from names
4. Permission Checks: Server permissions enforced for management

## Error Handling

All webhook errors inherit from `WebhookError`:

```python
from src.core.webhooks import (
    WebhookError,
    WebhookNotFoundError,
    WebhookAccessDeniedError,
    InvalidWebhookTokenError,
    WebhookNameError,
    WebhookAvatarError,
    WebhookLimitError,
    ChannelNotFoundError,
    PermissionDeniedError,
    InvalidContentError,
    EmbedLimitError,
)

try:
    webhooks.create_webhook(user_id, channel_id, name)
except WebhookLimitError as e:
    print(f"Limit reached: {e.current}/{e.max_allowed}")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
except WebhookNameError as e:
    print(f"Invalid name (max {e.max_length} chars)")
```

## Database Schema

Tables (prefixed with `webhook_`):
- `webhook_webhooks` - Webhook metadata and token hash
- `webhook_messages` - Webhook message tracking

## Testing

```bash
pytest src/tests/webhooks/ -v
```
