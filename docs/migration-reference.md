# Migration Reference

This document provides a detailed reference for all database migrations in Plexichat.

## Current Architecture

All tables are defined in schema module files under `src/core/*/schema.py`. Migration 000 imports and calls all 32 schema modules to create every table on a fresh database. Migration 001 is a selftest no-op used to verify migration apply/rollback endpoints.

## Migration Index

| Version | Name | Type | Description |
|---------|------|------|-------------|
| 000 | Initial Schema | Reversible | Creates all tables via schema module imports |
| 001 | Selftest Noop | Reversible | No-op migration for self-test system verification |

## Detailed Descriptions

### 000 - Initial Schema

**Purpose**: Creates the complete database schema for Plexichat on a fresh database.

**Impact**: High - Creates all tables.

**Tables Created** (via 32 schema modules):

| Schema Module | Tables |
|---------------|--------|
| `auth/schema.py` | auth_users, auth_sessions, auth_bots, auth_devices, auth_known_ips, auth_ip_blacklist, auth_email_tokens, auth_user_notes, auth_2fa_challenges, auth_external_accounts, auth_internal_secrets, auth_api_access_tokens, auth_api_access_token_scopes, auth_api_access_token_events, auth_audit_log, auth_passkeys, auth_passkey_challenges, username_blacklist, auth_deletion_records |
| `servers/schema.py` | srv_servers, srv_categories, srv_channels, srv_roles, srv_members, srv_member_roles, srv_channel_overrides, srv_invites, srv_bans, srv_audit_log, srv_scheduled_events, srv_event_rsvps, srv_templates, srv_template_data, srv_welcome_screens, srv_onboarding_steps, srv_onboarding_progress |
| `messaging/schema.py` | msg_conversations, msg_participants, msg_messages, msg_forwarded, msg_scheduled, user_bookmarks, user_profiles, msg_message_status, msg_pinned, msg_attachments, msg_content_filters, msg_user_settings, msg_dm_lookup, msg_edit_history, channel_ratchet_intervals |
| `threads/schema.py` | thread_threads, thread_members, thread_messages |
| `stickers/schema.py` | sticker_packs, sticker_stickers, sticker_usage |
| `notifications/schema.py` | notif_notifications, notif_settings, notif_channel_overrides, notif_unread |
| `relationships/schema.py` | rel_friends, rel_friend_requests, rel_blocked |
| `reactions/schema.py` | react_reactions, react_custom_emoji |
| `webhooks/schema.py` | webhook_webhooks, webhook_messages |
| `embeds/schema.py` | embed_embeds, embed_fields, embed_message_embeds, embed_preview_cache, embed_preview_rate_limits |
| `presence/schema.py` | pres_presence, pres_custom_status, pres_activity, pres_typing |
| `settings/schema.py` | user_settings, application_settings |
| `features/schema.py` | user_features, user_feature_usage, user_features_audit |
| `polls/schema.py` | poll_polls, poll_options, poll_votes |
| `voice/schema.py` | voice_states, voice_channel_settings, voice_stage_instances, voice_speaker_requests, voice_server_settings |
| `applications/schema.py` | app_applications, app_commands, app_installations, app_approved_bots, app_bot_requests, app_bot_profiles, app_oauth_codes, app_oauth_tokens, app_interactions, app_webhook_deliveries |
| `soundboard/schema.py` | soundboard_sounds, soundboard_permissions, soundboard_usage, soundboard_user_cooldowns |
| `media/schema.py` | media_files, media_thumbnails, media_proxy_cache |
| `media/deduplication` | media_file_hashes, media_hash_reports, media_blocked_hashes, media_blocked_users |
| `media/chunked.py` | media_upload_sessions |
| `reports/schema.py` | reports, message_reports, user_reports |
| `feedback/__init__.py` | feedback |
| `avatars/schema.py` | user_avatars, server_icons |
| `telemetry/__init__.py` | telemetry_response_times |
| `search/schema.py` | search_message_index, search_user_index, search_server_index, search_server_listings, search_categories, search_bump_history, search_history, saved_searches |
| `automod/schema.py` | automod_rules, automod_violations, automod_audit, automod_reputation, automod_exemptions, automod_rate_tracking |
| `admin/schema.py` | admin_users, admin_sessions, admin_notes, admin_roles, admin_role_assignments, admin_audit_log, admin_approvals, admin_notes_versioning, admin_approval_comments |
| `dmspam/schema.py` | dm_spam_filters, dm_spam_events |
| `chat_tracking/schema.py` | webhook_retry_queue, push_tokens, user_last_chat, user_recent_chats |
| `plexijoin/schema.py` | plexijoin_connections, plexijoin_inbound_requests, plexijoin_traffic_log |
| `dsar/schema.py` | dsar_requests, dsar_export_manifest |
| `artifacts/schema.py` | artifacts, voice_calls, artifact_ops, server_artifact_settings |

**Considerations**: This is the foundation migration. On a fresh database, this single migration creates the entire schema.

---

### 001 - Selftest Noop

**Purpose**: No-op migration for self-test system verification.

**Impact**: Low - No database changes.

**Changes**: None. This migration is a no-op.

**Considerations**: Designed purely for the self-test system to verify that apply and rollback operations work correctly.

---

## Schema Module Files

All table definitions live in schema module files. Each module exposes a `create_tables(db)` function that uses `CREATE TABLE IF NOT EXISTS` and is idempotent.

| Module | File |
|--------|------|
| Auth | `src/core/auth/schema.py` |
| Servers | `src/core/servers/schema.py` |
| Messaging | `src/core/messaging/schema.py` |
| Threads | `src/core/threads/schema.py` |
| Stickers | `src/core/stickers/schema.py` |
| Notifications | `src/core/notifications/schema.py` |
| Relationships | `src/core/relationships/schema.py` |
| Reactions | `src/core/reactions/schema.py` |
| Webhooks | `src/core/webhooks/schema.py` |
| Embeds | `src/core/embeds/schema.py` |
| Presence | `src/core/presence/schema.py` |
| Settings | `src/core/settings/schema.py` |
| Features | `src/core/features/schema.py` |
| Polls | `src/core/polls/schema.py` |
| Voice | `src/core/voice/schema.py` |
| Applications | `src/core/applications/schema.py` |
| Soundboard | `src/core/soundboard/schema.py` |
| Media | `src/core/media/schema.py` |
| Media Dedup | `src/core/media/deduplication` |
| Media Chunked | `src/core/media/chunked.py` |
| Reports | `src/core/reports/schema.py` |
| Feedback | `src/core/feedback/__init__.py` |
| Avatars | `src/core/avatars/schema.py` |
| Telemetry | `src/core/telemetry/__init__.py` |
| Search | `src/core/search/schema.py` |
| AutoMod | `src/core/automod/schema.py` |
| Admin | `src/core/admin/schema.py` |
| DM Spam | `src/core/dmspam/schema.py` |
| Chat Tracking | `src/core/chat_tracking/schema.py` |
| PlexiJoin | `src/core/plexijoin/schema.py` |
| DSAR | `src/core/dsar/schema.py` |
| Artifacts | `src/core/artifacts/schema.py` |

## SQLite vs PostgreSQL

Schema files use `db.convert_schema()` to handle dialect differences:
- `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGINT PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY`
- `INTEGER` → `BIGINT`
- `BLOB` → `BYTEA`
- `BOOLEAN DEFAULT 0/1` → `BOOLEAN DEFAULT FALSE/TRUE`

## Checking Migration Status

1. Navigate to the admin panel at `/api/v1/admin/ui-migrations`
2. View the migration list showing applied/pending/failed migrations
3. Click "Details" on any migration to see logs and metadata
