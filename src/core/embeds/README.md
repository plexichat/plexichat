# Embeds module

Create and manage rich embeds for PlexiChat messages.

## Overview

The embeds module provides rich embeds with fields, images, authors, footers, and URL previews (OpenGraph and Twitter Card). It handles validation, sanitization, and permission checks before persisting embeds to the database.

Embeds are attached to messages. A single message can have up to 10 embeds, each with up to 25 fields and a total character budget of 6000 characters.

## Setup

Initialize the module once at application startup. The `messaging` and `servers` modules are optional but enable message access and permission checks.

```python
from src.core.database import Database
from src.core import auth, messaging, servers, embeds

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)

embeds.setup(db, messaging, servers)
```

`embeds.setup()` is defined in `__init__.py:94`.

## Usage

### Create a rich embed

```python
from src.core import embeds

# Basic embed
embed = embeds.create_embed(
    user_id=1,
    title="Welcome!",
    description="This is a rich embed.",
    color="#FF5733",
)

# Full-featured embed
embed = embeds.create_embed(
    user_id=1,
    title="Full featured embed",
    description="Description text (max 4096 characters).",
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
        {"name": "Field 3", "value": "Value 3", "inline": False},
    ],
)
```

### Attach embeds to a message

```python
embed = embeds.create_embed(user_id=1, title="Announcement", description="Important info")
embeds.attach_embed_to_message(user_id=1, message_id=msg.id, embed_id=embed.id)

# Get all embeds on a message
message_embeds = embeds.get_message_embeds(user_id=1, message_id=msg.id)
```

### Update and remove embeds

```python
embed = embeds.update_embed(
    user_id=1,
    embed_id=embed.id,
    title="Updated title",
    description="New description",
)

embeds.remove_embed_from_message(user_id=1, message_id=msg.id, embed_id=embed.id)
embeds.delete_embed(user_id=1, embed_id=embed.id)
```

### Suppress embeds

Hide or show all embeds on a message (for example, to hide URL previews):

```python
embeds.suppress_embeds(user_id=1, message_id=msg.id)
embeds.unsuppress_embeds(user_id=1, message_id=msg.id)
```

### Create URL preview embeds

```python
# Create a preview and optionally attach it to a message
preview = embeds.create_url_preview(
    user_id=1,
    url="https://youtube.com/watch?v=abc123",
    message_id=msg.id,
)

# Parse URL metadata without creating an embed
metadata = embeds.parse_url_metadata("https://github.com/user/repo")
# {"url": "...", "title": "...", "description": "...", "image": "...", "site_name": "GitHub"}
```

### Validate and sanitize

```python
result = embeds.validate_embed(embed_data)
if result["valid"]:
    embed = embeds.create_embed(user_id=1, **embed_data)
else:
    print(result["issues"])
    print(result["total_chars"])

safe_content = embeds.sanitize_embed_content(user_input)
```

`validate_embed` is defined in `__init__.py:256` and `sanitize_embed_content` in `__init__.py:266`. The underlying logic lives in `validator.py:762`.

## Field limits

| Field | Limit |
|-------|-------|
| `title` | 256 characters |
| `description` | 4096 characters |
| `field.name` | 256 characters |
| `field.value` | 1024 characters |
| `footer.text` | 2048 characters |
| `author.name` | 256 characters |
| `fields` | 25 per embed |
| Embeds per message | 10 |
| Total characters per embed | 6000 |

Limits are defined in `validator.py:27`. `calculate_embed_chars` (`validator.py:726`) sums `title`, `description`, `footer.text`, `author.name`, and all `fields` names and values.

## Embed types

| Type | Description |
|------|-------------|
| `rich` | Standard rich embed (default) |
| `image` | Image-focused embed |
| `video` | Video embed (YouTube and similar) |
| `gifv` | Animated GIF embed |
| `article` | Article or blog post embed |
| `link` | Generic link preview |

Types are defined in `models.py` as `EmbedType`.

## URL validation

All URLs in embeds must meet these requirements:

* Use `http://` or `https://` (or a server-relative path under `/api/v1/media/`).
* Not use `javascript:`, `data:`, or `vbscript:` schemes.
* Not contain attribute-breakout characters (`"`, `'`, `<`, `>`, `` ` ``) or control characters.
* Pass shape validation (`validator.py:57`) and SSRF checks through `src.utils.security.URLValidator` (`validator.py:305`).

Relative media paths such as `/api/v1/media/...` are accepted and lifted to an absolute URL using `public_server_url` when available. See `validator.py:86`.

Invalid URLs raise `InvalidUrlError`. See `validator.py:192`.

## Security

1. **Cross-site scripting (XSS) prevention** — `sanitize_content` (`validator.py:423`) checks raw, decoded, and normalized forms of content against blocklists for `<script>`, event handlers, and active-content tags. See `validator.py:131` for the full pattern list.
2. **URL validation** — Only `http` and `https` URLs are allowed, with SSRF protection for private and loopback ranges.
3. **Content limits** — Strict character and field limits prevent abuse.
4. **Permission checks** — Server channels require the `messages.embed_links` permission to attach embeds. The check is performed through the `servers` module passed to `setup()`.

## Permission integration

| Permission | Description |
|------------|-------------|
| `messages.embed_links` | Required to attach embeds to messages in server channels. |

## Error handling

All embed errors inherit from `EmbedError` (`exceptions.py`):

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
    print(e.issues)

try:
    embeds.attach_embed_to_message(user_id, msg_id, embed_id)
except EmbedLimitError as e:
    print(f"Max {e.max_allowed} embeds reached (current: {e.current})")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
```

## Database schema

Tables are prefixed with `embed_` and defined in `schema.py`:

* `embed_embeds` — Embed metadata and content.
* `embed_fields` — Embed fields (name and value pairs).
* `embed_message_embeds` — Message-to-embed associations.

## Testing

Run the embed tests with:

```bash
pytest src/tests/embeds/ -v
```
