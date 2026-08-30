# DSAR collectors

Domain-specific collectors that gather personal data for DSAR exports (GDPR Article 20).

## Overview

Each collector is responsible for one data domain. The `DataCollector` orchestrator in `../collector.py:40` instantiates all collectors and delegates to them when `collect_all(user_id)` is called. This keeps export logic isolated by domain and makes it straightforward to add or modify a category.

All collectors inherit from a common base class that provides `fetch_all`, `fetch_one`, and `serialize_rows` helpers, handle their own error logging, and return empty lists or dictionaries on failure so a single failing domain does not abort the entire export.

## Collectors

| Collector | Domain | Tables |
|-----------|--------|--------|
| `identity` | User identity and authentication | `auth_users` |
| `sessions` | Sessions, devices, and known IPs | `auth_sessions`, `auth_devices`, `auth_known_ips` |
| `profile` | Profiles, settings, status, and activity | `user_profiles`, `user_settings`, `msg_content_filters`, `msg_user_settings`, `pres_custom_status`, `pres_activity` |
| `messages` | Messages, conversations, and bookmarks | `msg_messages`, `msg_participants`, `msg_conversations`, `msg_forwarded`, `msg_scheduled`, `msg_edit_history`, `user_bookmarks` |
| `relationships` | Friends, friend requests, and blocks | `rel_friends`, `rel_friend_requests`, `rel_blocked` |
| `servers` | Server memberships and onboarding progress | `srv_members`, `srv_onboarding_progress` |
| `content` | Pins, reactions, and attachments | `msg_pinned`, `react_reactions`, `msg_attachments` |
| `notifications` | Notifications, settings, and channel overrides | `notif_notifications`, `notif_unread`, `notif_settings`, `notif_channel_overrides` |
| `oauth` | External OAuth accounts | `auth_external_accounts` |
| `applications` | Owned apps, installations, and OAuth tokens | `app_applications`, `app_installations`, `app_oauth_tokens` |
| `reports` | Message and user reports (both sides) | `message_reports`, `user_reports` |
| `feedback` | User feedback | `feedback` |
| `search` | Search history and saved searches | `search_history`, `saved_searches` |
| `features` | Feature flags, usage, and audit log | `user_features`, `user_feature_usage`, `user_features_audit` |
| `polls` | Poll votes and created polls | `poll_votes`, `poll_polls` |
| `voice` | Voice states, calls, artifacts, and transcripts | `voice_states`, `voice_calls`, `artifacts` |
| `automod` | Violations, reputation, and exemptions | `automod_violations`, `automod_reputation`, `automod_exemptions` |
| `presence` | Presence and typing indicators | `pres_presence`, `pres_typing` |
| `stickers` | Sticker usage | `sticker_usage` |
| `soundboard` | Soundboard usage | `soundboard_usage` |
| `media` | Media files, avatars, and API tokens | `media_files`, `user_avatars`, `auth_api_access_tokens` |
| `suite` | Suite workbooks, stash, and audit data | `stash_items`, `stash_collections`, `sum_workbooks`, `suite_audit_log`, and others |

## Architecture

```
collectors/
├── __init__.py          # Re-exports all collectors
├── identity.py          # IdentityCollector
├── sessions.py          # SessionsCollector
├── profile.py           # ProfileCollector
├── messages.py          # MessagesCollector
├── relationships.py     # RelationshipsCollector
├── servers.py           # ServersCollector
├── content.py           # ContentCollector
├── notifications.py     # NotificationsCollector
├── oauth.py             # OAuthCollector
├── applications.py      # ApplicationsCollector
├── reports.py           # ReportsCollector
├── feedback.py          # FeedbackCollector
├── search.py            # SearchCollector
├── features.py          # FeaturesCollector
├── polls.py             # PollsCollector
├── voice.py             # VoiceCollector
├── automod.py           # AutomodCollector
├── presence.py          # PresenceCollector
├── stickers.py          # StickersCollector
├── soundboard.py        # SoundboardCollector
├── media.py             # MediaCollector
└── suite.py             # SuiteCollector
```

### Collector contract

Each collector:

* Inherits from `BaseCollector` and implements `collect(db, user_id) -> Dict[str, Any]`.
* Redacts sensitive fields such as passwords, token secrets, and encrypted blobs. For example, encrypted values are returned as `(encrypted)` and avatar binary data is excluded.
* Logs errors internally and returns an empty result for its domain on failure, so the overall export can still complete. Errors are surfaced in `export_metadata.warnings` by `DataCollector` (`../collector.py:98`).

### Orchestration

`DataCollector` (`../collector.py:40`) maintains the same public API as the original monolithic collector:

```python
from src.core.dsar.collector import DataCollector

collector = DataCollector(db)
data = collector.collect_all(user_id=12345)
# data is a dict with keys: exported_at, user_id, export_version, plus one key per category
counts = collector.count_records(user_id=12345)
```

`collect_all` iterates over all registered collectors, calls `collect(user_id)` on each, and aggregates collection errors into `export_metadata.warnings` with `complete: false` when any category fails.

## Add a new collector

1. Create a new file in this directory (for example, `my_domain.py`).
2. Subclass `BaseCollector` and implement `collect(user_id)`.
3. Register the collector in `DataCollector.__init__` (`../collector.py:49`).
4. Add corresponding tables to `count_records` in `../collector.py:113` if you want preview counts.
5. Add tests that verify the collector returns the expected shape and redacts sensitive fields.
