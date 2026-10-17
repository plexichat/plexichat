# Presence tests

Tests for user presence, including status, activity, typing indicators, and visibility
rules.

## Test files

- `test_activity.py` — Verifies setting, getting, clearing, and updating activities
  (PLAYING, STREAMING, LISTENING, WATCHING, COMPETING, CUSTOM) with timestamps and
  assets.
- `test_edge_cases.py` — Covers invalid-user errors, invalid-activity errors, default
  presence values, rapid status/activity/typing changes, timestamp precision, and
  empty-query handling.
- `test_integration.py` — Tests presence set/get operations, multi-user presence
  retrieval, status clearing, and `update_last_seen`.
- `test_status.py` — Validates all status transitions (ONLINE, IDLE, DND, INVISIBLE,
  OFFLINE), custom-status lifecycle, and `last_seen`/`updated_at` timestamp updates.
- `test_typing.py` — Tests typing-indicator start/stop, multi-channel support,
  expiration timing (6-second timeout), and multi-user scenarios.
- `test_visibility.py` — Verifies invisible-mode visibility rules, block-based presence
  hiding, own-presence visibility, and bulk visibility retrieval.
