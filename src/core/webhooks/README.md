# Webhooks module

The webhooks module provides an API for creating, managing, and executing webhooks that post automated messages to channels.

## Features

- Create webhooks with secure token generation
- Manage webhooks (get, update, delete, regenerate token)
- Execute webhooks to send messages with username and avatar overrides
- Support rich embeds (up to 10 per message) and thread posting
- Enforce permission-based access control
- Store tokens as SHA-256 hashes

## Setup

Initialize the webhooks module after its dependencies are ready:

```python
from src.core.database import Database
from src.core import auth, messaging, servers, embeds, webhooks

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
embeds.setup(db, messaging, servers)

webhooks.setup(db, auth, messaging, servers, embeds)
```

## Create a webhook

```python
from src.core import webhooks

webhook = webhooks.create_webhook(
    user_id=1,
    channel_id=123,
    name="My Webhook",
    avatar_url="https://example.com/avatar.png",  # optional
)

# The token is only shown at creation time.
print(f"Token: {webhook.token}")
print(f"URL: {webhook.url}")
```

## Retrieve webhooks

```python
# Get a single webhook
webhook = webhooks.get_webhook(webhook_id=456, user_id=1)

# Get all webhooks for a channel
channel_webhooks = webhooks.get_channel_webhooks(user_id=1, channel_id=123)

# Get all webhooks for a server
server_webhooks = webhooks.get_server_webhooks(user_id=1, server_id=789)
```

## Update a webhook

```python
# Update name and avatar
webhook = webhooks.update_webhook(
    user_id=1,
    webhook_id=456,
    name="New Name",
    avatar_url="https://example.com/new-avatar.png",
)

# Move to a different channel
webhook = webhooks.update_webhook(
    user_id=1,
    webhook_id=456,
    channel_id=new_channel_id,
)

# Clear the avatar
webhook = webhooks.update_webhook(
    user_id=1,
    webhook_id=456,
    avatar_url="",
)
```

## Delete a webhook

```python
webhooks.delete_webhook(user_id=1, webhook_id=456)
```

## Regenerate a token

Regenerating a token invalidates the previous one:

```python
webhook = webhooks.regenerate_token(user_id=1, webhook_id=456)
print(f"New token: {webhook.token}")
```

## Execute a webhook

```python
# Send a simple message
webhooks.execute_webhook(
    webhook_id=456,
    token="webhook.456.abc123...",
    content="Hello from webhook!",
)

# Override the username and avatar
webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",  # the secret part of the token also works
    content="Custom message",
    username="Custom Bot",
    avatar_url="https://example.com/custom.png",
)

# Send with embeds
webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",
    embeds=[{
        "title": "Embed Title",
        "description": "Embed description",
        "color": "#FF0000",
    }],
)

# Post to a thread
webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",
    content="Thread message",
    thread_id=thread_id,
)

# Wait for the message to be created and return it
message = webhooks.execute_webhook(
    webhook_id=456,
    token="abc123...",
    content="Hello!",
    wait=True,
)
print(f"Message ID: {message.id}")
```

### Execute by URL

```python
message = webhooks.execute_webhook_by_url(
    webhook_url="/webhooks/456/abc123...",
    content="Hello via URL!",
    wait=True,
)
```

## Token format

Tokens follow the format `webhook.{webhook_id}.{random_secret}`.

The token is returned only when you create a new webhook or regenerate an existing one.

## Webhook URL format

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

## Permissions

The module requires the `webhooks.manage` permission for:

- Creating, viewing, updating, and deleting webhooks
- Regenerating tokens

Anyone with the webhook token can execute the webhook; the token acts as the credential.

## Security

- **Token hashing**: only the SHA-256 hash is stored, not the raw token
- **URL validation**: avatar URLs must use `http` or `https`; private/loopback addresses are blocked (SSRF protection)
- **Content sanitization**: HTML tags are stripped from names
- **Permission checks**: server permissions are enforced for all management operations

## Error handling

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

## Database schema

Tables (prefixed with `webhook_`):

- `webhook_webhooks` -- webhook metadata and token hash
- `webhook_messages` -- webhook message tracking

## Testing

```bash
pytest src/tests/webhooks/ -v
```
