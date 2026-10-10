# Notifications Tests

This directory contains tests for the notification system, which handles
mention parsing, user preferences, per-channel overrides, and notification
delivery.

## Test files

| File | Description |
|------|-------------|
| `test_user_mentions.py` | @user mention parsing, validation, and notification creation |
| `test_role_mentions.py` | @role mention parsing and suppress-roles setting |
| `test_channel_mentions.py` | Parsing for user, role, channel, @everyone, and @here mentions |
| `test_preferences.py` | Notification level, DM, mobile push, and suppress-everyone settings |
| `test_channel_overrides.py` | Per-channel mute, mentions-only, and expiration behavior |
| `test_unread_counts.py` | Unread count tracking, mark-all-read, and server/channel scoping |
| `test_notification_feed.py` | Notification feed ordering, pagination, and count stability |
| `test_edge_cases.py` | Malformed mentions, empty content, large payloads, and error handling |
| `test_integration.py` | End-to-end notification fan-out through the message send path |

## Shared fixtures

The `conftest.py` file defines reusable fixtures:

- `wired_send_path` -- Registers the notification manager as the module-level
  API so the production send path performs mention fan-out during tests.
- `users_with_dm` -- Creates two users with a DM conversation.
- `fresh_users` -- Creates isolated users for tests requiring no shared state.
- `users_with_server` -- Creates three users with a server, channel, and roles.
- `group_conversation` -- Creates a group DM for testing notification delivery.

## Key behaviors tested

- **Mention deduplication:** Multiple mentions of the same user in a single
  message produce only one notification.
- **Self-mention suppression:** Users do not receive notifications for
  mentioning themselves.
- **Channel override effects:** Muted or NOTHING-level channel overrides
  block all notifications, including @mentions.
- **Mute expiration:** Expired mutes re-enable notification delivery.
- **Feed count stability:** `total_count` and `unread_count` remain constant
  across paginated feed requests.
- **Content preview truncation:** Long message previews are truncated to
  100 characters with an ellipsis.
