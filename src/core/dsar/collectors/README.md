# DSAR Collectors

Domain-specific data collectors for GDPR Article 20 Data Portability compliance.

## Collectors Overview

| Collector | Domain | Tables |
|-----------|--------|--------|
| `identity` | User identity & auth | `auth_users` |
| `sessions` | Sessions, devices, known IPs | `auth_sessions`, `auth_devices`, `auth_known_ips` |
| `profile` | Profiles, settings, status, activity | `user_profiles`, `user_settings`, `msg_content_filters`, `msg_user_settings`, `pres_custom_status`, `pres_activity` |
| `messages` | Messages, conversations, bookmarks | `msg_messages`, `msg_participants`, `msg_conversations`, `msg_forwarded`, `msg_scheduled`, `msg_edit_history`, `user_bookmarks` |
| `relationships` | Friends, requests, blocks | `rel_friends`, `rel_friend_requests`, `rel_blocked` |
| `servers` | Server memberships, onboarding | `srv_members`, `srv_onboarding_progress` |
| `content` | Pins, reactions, attachments | `msg_pinned`, `react_reactions`, `msg_attachments` |
| `notifications` | Notifications, settings, overrides | `notif_notifications`, `notif_unread`, `notif_settings`, `notif_channel_overrides` |
| `oauth` | External OAuth accounts | `auth_external_accounts` |
| `applications` | Owned apps, installs, tokens | `app_applications`, `app_installations`, `app_oauth_tokens` |
| `reports` | Message/user reports (both sides) | `message_reports`, `user_reports` |
| `feedback` | User feedback | `feedback` |
| `search` | Search history, saved searches | `search_history`, `saved_searches` |
| `features` | Feature flags, usage, audit | `user_features`, `user_feature_usage`, `user_features_audit` |
| `polls` | Poll votes, created polls | `poll_votes`, `poll_polls` |
| `voice` | Voice states, calls, artifacts, transcripts | `voice_states`, `voice_calls`, `artifacts` |
| `automod` | Violations, reputation, exemptions | `automod_violations`, `automod_reputation`, `automod_exemptions` |
| `presence` | Presence, typing | `pres_presence`, `pres_typing` |
| `stickers` | Sticker usage | `sticker_usage` |
| `soundboard` | Soundboard usage | `soundboard_usage` |
| `media` | Media files, avatars, API tokens | `media_files`, `user_avatars`, `auth_api_access_tokens` |

## Architecture

Each collector:
- Inherits from `BaseCollector` (provides `fetch_all`, `fetch_one`, `serialize_rows`)
- Implements a single `collect(db, user_id) -> Dict[str, Any]` method
- Handles its own error logging and returns empty lists/dicts on failure
- Redacts sensitive fields (passwords, tokens, encrypted data)

The `DataCollector` class in `../collector.py` orchestrates all collectors, maintaining the same public API as the original monolithic implementation.