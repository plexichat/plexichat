# Sticker tests

Tests for the sticker system, including pack management, sticker uploads, suggestions,
and usage tracking.

## Test files

- `test_packs.py` — Verifies sticker pack creation, retrieval, deletion, name
  validation, and permission enforcement.
- `test_suggestions.py` — Tests suggestion matching by name, tags, and related emoji,
  empty content handling, result limits, and relevance sorting.
- `test_upload.py` — Covers adding stickers (PNG, APNG, Lottie), name and size
  validation, retrieval, and deletion.
- `test_usage.py` — Validates usage tracking when stickers are sent in messages and
  verifies that usage counts increment correctly.
