# Reactions Tests

These tests verify the message reactions module, including adding, removing,
counting, and managing emoji reactions on messages. They cover Unicode and
custom emoji, permissions, limits, and integration with the messaging and
servers modules.

## Test files

- `test_add_remove.py` -- Add, remove, and toggle reactions on messages.
- `test_counts.py` -- Aggregate reaction counts and list users who reacted.
- `test_custom_emoji.py` -- Create, retrieve, delete, and validate custom emoji.
- `test_edge_cases.py` -- Handle missing messages, invalid emoji, duplicates,
  and access-control edge cases.
- `test_integration.py` -- Integrate with the messaging and servers modules,
  including DM, group, and server-channel contexts.
- `test_limits.py` -- Enforce per-message and per-page reaction limits.
- `test_permissions.py` -- Check server-channel and group-conversation
  permissions for adding and moderating reactions.
- `test_unicode_emoji.py` -- Validate Unicode emoji shortcodes, raw sequences,
  and reject arbitrary text.
