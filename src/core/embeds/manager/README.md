# Embed Manager Package

Mixin-based architecture for embed operations.

## Structure

- `base.py` - `EmbedManagerBase`: core infrastructure, row conversion, permission helpers
- `crud.py` - `EmbedCRUDMixin`: create, update, delete operations
- `attachment.py` - `EmbedAttachmentMixin`: message-embed associations
- `url_preview.py` - `EmbedURLPreviewMixin`: URL metadata scraping
- `validation.py` - `EmbedValidationMixin`: validation re-exports for tests
- `composer.py` - `EmbedManager`: composed final class
- `__init__.py` - re-exports `EmbedManager` from composer

## Composition

```python
class EmbedManager(
    EmbedValidationMixin,
    EmbedCRUDMixin,
    EmbedAttachmentMixin,
    EmbedURLPreviewMixin,
    EmbedManagerBase,
):
    ...
```

## Dependencies

Mixins declare their dependencies via type annotations on the class body. Methods from sibling mixins are accessed through the MRO at runtime; type annotations ensure type checkers can resolve calls.

## API

All public methods from the original monolithic class are preserved:
- `get_embed`, `create_embed`, `update_embed`, `delete_embed`
- `attach_embed_to_message`, `remove_embed_from_message`, `get_message_embeds`
- `suppress_embeds`, `unsuppress_embeds`
- `create_url_preview`, `parse_url_metadata`
- `validate_embed`, `sanitize_embed_content`, `validate_embed_data`, `sanitize_content`