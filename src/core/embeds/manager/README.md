# Embed manager

The embed manager handles creation, validation, attachment, and URL preview generation for message embeds in PlexiChat.

This package uses a mixin-based architecture so that each concern is isolated in its own module and then composed into a single `EmbedManager` class. You interact only with `EmbedManager`; you don't need to use the mixins directly.

## Before you begin

This package depends on a connected database instance and optionally on the messaging, servers, and media proxy modules for permission checks and link previews.

## Architecture

### File structure

| File | Class | Responsibility |
| --- | --- | --- |
| `base.py` | `EmbedManagerBase` | Core infrastructure, row-to-model conversion, and permission helpers |
| `crud.py` | `EmbedCRUDMixin` | Create, update, and delete operations |
| `attachment.py` | `EmbedAttachmentMixin` | Message-embed associations and suppression |
| `url_preview.py` | `EmbedURLPreviewMixin` | URL metadata scraping and preview generation |
| `validation.py` | `EmbedValidationMixin` | Validation wrapper that re-exports validator functions for tests |
| `composer.py` | `EmbedManager` | Final composed class that combines all mixins |
| `__init__.py` | — | Re-exports `EmbedManager` from `composer.py` |
| `protocol.py` | `EmbedManagerProtocol` | Type protocol that declares cross-mixin dependencies |

### Class composition

`EmbedManager` inherits from the mixins in the following order. Method resolution follows Python's method resolution order (MRO) at runtime.

```python
from src.core.embeds.manager import EmbedManager

class EmbedManager(
    EmbedValidationMixin,
    EmbedCRUDMixin,
    EmbedAttachmentMixin,
    EmbedURLPreviewMixin,
    EmbedManagerBase,
):
    ...
```

Initialize the manager with your database and optional service modules:

```python
from src.core.embeds.manager import EmbedManager

manager = EmbedManager(
    db=db,
    messaging_module=messaging,
    servers_module=servers,
    media_proxy=media_proxy,
)
```

### Dependencies between mixins

Mixins declare dependencies on sibling methods through type annotations on `EmbedManagerProtocol` (`protocol.py:1`). At runtime, calls are resolved through the MRO. Type checkers use the protocol annotations to verify cross-mixin calls.

## API reference

All public methods from the original monolithic class are preserved on `EmbedManager`.

### Base operations

Defined in `EmbedManagerBase` (`base.py:20`):

- `get_embed(embed_id)` — Retrieve a single embed by ID. Returns `None` if not found.
- `_row_to_embed(row)` — Convert a database row to an `Embed` model. Used internally.

Helper methods for permission and lookup:

- `_get_message(message_id)`
- `_is_participant(conversation_id, user_id)`
- `_get_channel_for_conversation(conversation_id)`
- `_check_embed_links_permission(user_id, server_id, channel_id)`

### CRUD operations

Defined in `EmbedCRUDMixin` (`crud.py:18`):

- `create_embed(user_id, title, description, url, ...)` — Create a new embed. Validates input and optionally attaches to a message. Raises `EmbedValidationError` if validation fails.
- `update_embed(user_id, embed_id, ...)` — Update an existing embed. Only the creator can update. Raises `EmbedNotFoundError` or `PermissionDeniedError`.
- `delete_embed(user_id, embed_id)` — Delete an embed and its message associations.

### Attachment operations

Defined in `EmbedAttachmentMixin` (`attachment.py:20`):

- `attach_embed_to_message(user_id, message_id, embed_id, position)` — Attach an embed to a message at an optional position.
- `remove_embed_from_message(user_id, message_id, embed_id)` — Remove an embed from a message.
- `get_message_embeds(user_id, message_id)` — List unsuppressed embeds attached to a message.
- `are_embeds_suppressed(message_id)` — Check whether all embeds on a message are suppressed.
- `suppress_embeds(user_id, message_id)` — Hide all embeds on a message.
- `unsuppress_embeds(user_id, message_id)` — Show all embeds on a message.

### URL preview operations

Defined in `EmbedURLPreviewMixin` (`url_preview.py:19`):

- `create_url_preview(user_id, url, message_id)` — Create an embed from Open Graph or Twitter Card metadata using `LinkPreviewService` with SSRF protection and rate limiting.
- `parse_url_metadata(url, user_id)` — Fetch and sanitize URL metadata without creating an embed. Returns sanitized fields or an error object.
- `_scrape_url_metadata(user_id, url)` — Internal override point for tests that delegates to `LinkPreviewService`.

### Validation operations

Defined in `EmbedValidationMixin` (`validation.py:13`):

- `validate_embed(embed_data)` — Validate embed data. Returns a dict with `valid`, `issues`, `total_chars`, and `sanitized_data`.
- `validate_embed_data(embed_data)` — Alias for `validate_embed`.
- `sanitize_embed_content(content)` — Sanitize text content.
- `sanitize_content(content)` — Alias for `sanitize_embed_content`.
- `_validate_embed_data(embed_data)` — Internal method that returns a `ValidationResult` object. Used by CRUD and URL preview mixins.

## Related modules

- `src.core.embeds.validator` — Core validation logic and limits (`MAX_EMBEDS_PER_MESSAGE`, `MAX_FIELDS`, `TOTAL_CHAR_LIMIT`).
- `src.core.embeds.link_preview` — `LinkPreviewService` for secure fetching and caching of URL previews.
- `src.core.embeds.models` — `Embed` data models.
