# Embeds Module

Rich embed system for Plexichat API supporting rich embeds with fields, images, authors, footers, and URL previews.

## Features

- Rich embed creation with all standard fields
- Field limits and character validation
- URL preview embeds (OpenGraph/Twitter Card simulation)
- Attach/update/remove embeds from messages
- Suppress/unsuppress embeds (hide URL previews)
- Bot/webhook embeds vs user URL previews distinction
- XSS prevention and URL sanitization
- Permission integration with servers module

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import embeds

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)

# Initialize embeds
embeds.setup(db, messaging, servers)
```

## Usage

### Create Rich Embed

```python
from src.core import embeds

# Create a basic embed
embed = embeds.create_embed(
    user_id=1,
    title="Welcome!",
    description="This is a rich embed with multiple features.",
    color="#FF5733"
)

# Create embed with all fields
embed = embeds.create_embed(
    user_id=1,
    title="Full Featured Embed",
    description="Description text here (max 4096 chars)",
    url="https://example.com",
    timestamp="2025-01-15T12:00:00Z",
    color="#00FF00",
    footer={"text": "Footer text", "icon_url": "https://example.com/icon.png"},
    image={"url": "https://example.com/image.png", "width": 800, "height": 600},
    thumbnail={"url": "https://example.com/thumb.png"},
    author={"name": "Author Name", "url": "https://author.com", "icon_url": "https://author.com/avatar.png"},
    fields=[
        {"name": "Field 1", "value": "Value 1", "inline": True},
        {"name": "Field 2", "value": "Value 2", "inline": True},
        {"name": "Field 3", "value": "Value 3", "inline": False}
    ]
)
```

### Attach Embed to Message

```python
# Create embed and attach to message
embed = embeds.create_embed(user_id=1, title="Announcement", description="Important info")
embeds.attach_embed_to_message(user_id=1, message_id=msg.id, embed_id=embed.id)

# Get all embeds on a message
message_embeds = embeds.get_message_embeds(user_id=1, message_id=msg.id)
```

### Update and Remove Embeds

```python
# Update embed
embed = embeds.update_embed(
    user_id=1,
    embed_id=embed.id,
    title="Updated Title",
    description="New description"
)

# Remove embed from message
embeds.remove_embed_from_message(user_id=1, message_id=msg.id, embed_id=embed.id)

# Delete embed entirely
embeds.delete_embed(user_id=1, embed_id=embed.id)
```

### Suppress Embeds

```python
# Hide all embeds on a message (e.g., hide URL previews)
embeds.suppress_embeds(user_id=1, message_id=msg.id)

# Show embeds again
embeds.unsuppress_embeds(user_id=1, message_id=msg.id)
```

### URL Preview Embeds

```python
# Create URL preview embed
preview = embeds.create_url_preview(
    user_id=1,
    url="https://youtube.com/watch?v=abc123",
    message_id=msg.id  # Optional: auto-attach to message
)

# Parse URL metadata without creating embed
metadata = embeds.parse_url_metadata("https://github.com/user/repo")
print(metadata)
# {"url": "...", "title": "...", "description": "...", "image": "...", "site_name": "GitHub"}
```

### Validation

```python
# Validate embed data before creating
result = embeds.validate_embed(embed_data)
if result["valid"]:
    embed = embeds.create_embed(user_id=1, **embed_data)
else:
    print(f"Validation errors: {result['issues']}")
    print(f"Total characters: {result['total_chars']}")

# Sanitize content
safe_content = embeds.sanitize_embed_content(user_input)
```

## Field Limits

| Field | Max Length |
|-------|------------|
| title | 256 characters |
| description | 4096 characters |
| field.name | 256 characters |
| field.value | 1024 characters |
| footer.text | 2048 characters |
| author.name | 256 characters |
| fields | 25 maximum |
| embeds per message | 10 maximum |
| total characters | 6000 maximum |

## Embed Types

| Type | Description |
|------|-------------|
| rich | Standard rich embed (default) |
| image | Image-focused embed |
| video | Video embed (YouTube, etc.) |
| gifv | Animated GIF embed |
| article | Article/blog post embed |
| link | Generic link preview |

## URL Validation

All URLs must:
- Use http:// or https:// protocol
- Not contain javascript:, data:, or vbscript: schemes
- Be properly formatted

## Security Features

1. XSS Prevention: All content sanitized for script tags and event handlers
2. URL Validation: Only http/https URLs allowed
3. Content Limits: Strict character limits prevent abuse
4. Permission Checks: Server embed_links permission required

## Permission Integration

For server channels, the module checks:

| Permission | Description |
|------------|-------------|
| messages.embed_links | Required to attach embeds to messages |

## Error Handling

All embed errors inherit from `EmbedError`:

```python
from src.core.embeds import (
    EmbedError,
    EmbedNotFoundError,
    EmbedValidationError,
    EmbedLimitError,
    EmbedFieldLimitError,
    EmbedCharacterLimitError,
    InvalidUrlError,
    InvalidColorError,
    MessageNotFoundError,
    PermissionDeniedError,
    EmbedSanitizationError,
)

try:
    embeds.create_embed(user_id, title="x" * 300)
except EmbedValidationError as e:
    print(f"Validation failed: {e.issues}")

try:
    embeds.attach_embed_to_message(user_id, msg_id, embed_id)
except EmbedLimitError as e:
    print(f"Max {e.max_allowed} embeds reached (current: {e.current})")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
```

## Database Schema

Tables (prefixed with `embed_`):
- `embed_embeds` - Embed metadata and content
- `embed_fields` - Embed fields (name/value pairs)
- `embed_message_embeds` - Message-embed associations

## Testing

```bash
pytest src/tests/embeds/ -v
```
