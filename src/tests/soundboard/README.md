# Soundboard tests

Tests for the soundboard feature, including sound uploads, playback, cooldowns, and
permissions.

## Test files

- `test_cooldowns.py` — Verifies that sounds enter cooldown after playback and that
  cooldowns are independent per sound and per user.
- `test_permissions.py` — Validates role-based sound permission assignment, denial,
  updates, and owner-only enforcement.
- `test_playback.py` — Covers successful playback, usage tracking, and error handling
  for nonexistent sounds, text channels, and nonexistent channels.
- `test_upload.py` — Tests sound upload success paths, format support (MP3, OGG), name
  validation, size and duration limits, retrieval, and deletion.
