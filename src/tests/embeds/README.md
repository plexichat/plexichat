# Embeds Tests

These tests verify the rich-embed module, including embed creation, field
management, validation, sanitization, and integration with messages and URL
preview generation.

## Test files

- `test_author_footer.py` -- Set author and footer fields on embeds.
- `test_create_embed.py` -- Create embeds with title, description, URL,
  timestamp, color, images, thumbnails, providers, and all fields combined.
- `test_edge_cases.py` -- Handle edge cases such as missing fields, invalid
  data, and boundary conditions.
- `test_fields.py` -- Add, validate, and order embed fields, including
  inline layout and maximum-field limits.
- `test_images.py` -- Attach image and thumbnail URLs to embeds.
- `test_message_integration.py` -- Attach embeds to messages and retrieve
  them from the message store.
- `test_sanitization.py` -- Sanitize embed content to prevent injection
  attacks.
- `test_url_preview.py` -- Generate URL previews and validate preview
  metadata.
- `test_validation.py` -- Enforce character limits, validate URLs and colors,
  reject dangerous URL schemes, and enforce the 6000-character total limit.
