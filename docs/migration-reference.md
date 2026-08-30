# Migration reference

This document describes the database migrations in Plexichat and the schema modules that own each table.

## Current architecture

All tables are defined in schema module files under `src/core/*/schema.py`. Each module exposes a `create_tables(db)` function that uses `CREATE TABLE IF NOT EXISTS` and is idempotent. Migration `000` imports and calls every schema module to create the full schema on a fresh database. Migration `001` is a self-test no-op that verifies the migration apply and rollback endpoints.

The authoritative migration implementations are in `src/core/migrations/migrations/000_initial_schema.py:59` and `src/core/migrations/migrations/001_selftest_noop.py`.

## Migration index

| Version | Name | Type | Description |
| --- | --- | --- | --- |
| 000 | Initial schema | Reversible (guarded) | Creates all tables through schema module imports. |
| 001 | Self-test no-op | Reversible | No-op migration for self-test verification. |

## Detailed descriptions

### 000 — Initial schema

**Purpose:** Create the complete database schema on a fresh database.

**Impact:** High — Creates all tables.

**Behavior:** The `up(db)` function iterates over schema creators in dependency order and calls each `create_tables(db)`. When a creator fails, the migration raises the error immediately. Reference guards in `src/core/media/schema.py` are installed last, after every dependent domain table exists.

**Tables created through 32 schema modules:**

| Schema module | Tables |
| --- | --- |
| `src/core/auth/schema.py` | `auth_users`, `auth_sessions`, `auth_bots`, `auth_devices`, `auth_known_ips`, `auth_ip_blacklist`, `auth_email_tokens`, `auth_user_notes`, `auth_2fa_challenges`, `auth_external_accounts`, `auth_internal_secrets`, `auth_api_access_tokens`, `auth_api_access_token_scopes`, `auth_api_access_token_events`, `auth_audit_log`, `auth_passkeys`, `auth_passkey_challenges`, `username_blacklist`, `auth_deletion_records`, `auth_failed_logins`, `auth_ws_tickets` |
| `src/core/servers/schema.py` | `srv_servers`, `srv_categories`, `srv_channels`, `srv_roles`, `srv_members`, `srv_member_roles`, `srv_channel_overrides`, `srv_invites`, `srv_bans`, `srv_audit_log`, `srv_scheduled_events`, `srv_event_rsvps`, `srv_templates`, `srv_template_data`, `srv_welcome_screens`, `srv_onboarding_steps`, `srv_onboarding_progress` |
| `src/core/messaging/schema.py` | `msg_conversations`, `msg_participants`, `msg_messages`, `msg_forwarded`, `msg_scheduled`, `user_bookmarks`, `user_profiles`, `msg_message_status`, `msg_pinned`, `msg_attachments`, `msg_content_filters`, `msg_user_settings`, `msg_dm_lookup`, `msg_edit_history`, `channel_ratchet_intervals`, `msg_message_tokens` |
| `src/core/threads/schema.py` | `thread_threads`, `thread_members`, `thread_messages` |
| `src/core/stickers/schema.py` | `sticker_packs`, `sticker_stickers`, `sticker_usage` |
| `src/core/notifications/schema.py` | `notif_notifications`, `notif_settings`, `notif_channel_overrides`, `notif_unread` |
| `src/core/relationships/schema.py` | `rel_friends`, `rel_friend_requests`, `rel_blocked` |
| `src/core/reactions/schema.py` | `react_reactions`, `react_custom_emoji` |
| `src/core/webhooks/schema.py` | `webhook_webhooks`, `webhook_messages` |
| `src/core/embeds/schema.py` | `embed_embeds`, `embed_fields`, `embed_message_embeds`, `embed_preview_cache`, `embed_preview_rate_limits` |
| `src/core/presence/schema.py` | `pres_presence`, `pres_custom_status`, `pres_activity`, `pres_typing` |
| `src/core/settings/schema.py` | `user_settings`, `application_settings` |
| `src/core/features/schema.py` | `user_features`, `user_feature_usage`, `user_features_audit` |
| `src/core/polls/schema.py` | `poll_polls`, `poll_options`, `poll_votes`, `poll_voters` |
| `src/core/voice/schema.py` | `voice_states`, `voice_channel_settings`, `voice_stage_instances`, `voice_speaker_requests`, `voice_server_settings`, `voice_calls` |
| `src/core/applications/schema.py` | `app_applications`, `app_commands`, `app_installations`, `app_approved_bots`, `app_bot_requests`, `app_bot_profiles`, `app_oauth_codes`, `app_oauth_tokens`, `app_interactions`, `app_webhook_deliveries` |
| `src/core/soundboard/schema.py` | `soundboard_sounds`, `soundboard_permissions`, `soundboard_usage`, `soundboard_user_cooldowns` |
| `src/core/media/schema.py` | `media_files`, `media_thumbnails`, `media_proxy_cache`, `media_file_deletion_locks`, `media_file_deletion_tombstones` |
| `src/core/media/deduplication` | `media_file_hashes`, `media_hash_reports`, `media_blocked_hashes`, `media_blocked_users` |
| `src/core/media/chunked.py` | `media_upload_sessions` |
| `src/core/reports/schema.py` | `reports`, `message_reports`, `user_reports` |
| `src/core/feedback/__init__.py` | `feedback` |
| `src/core/avatars/schema.py` | `user_avatars`, `server_icons` |
| `src/core/telemetry/__init__.py` | `telemetry_response_times` |
| `src/core/search/schema.py` | `search_message_index`, `search_messages_fts*`, `search_user_index`, `search_users_fts*`, `search_server_index`, `search_servers_fts*`, `search_server_listings`, `search_categories`, `search_bump_history`, `search_history`, `saved_searches` |
| `src/core/automod/schema.py` | `automod_rules`, `automod_violations`, `automod_audit`, `automod_reputation`, `automod_exemptions`, `automod_rate_tracking` |
| `src/core/admin/schema.py` | `admin_users`, `admin_sessions`, `admin_notes`, `admin_notes_versioning`, `admin_roles`, `admin_role_assignments`, `admin_audit_log`, `admin_approvals`, `admin_approval_comments` |
| `src/core/dmspam/schema.py` | `dm_spam_filters`, `dm_spam_events` |
| `src/core/chat_tracking/schema.py` | `webhook_retry_queue`, `push_tokens`, `user_last_chat`, `user_recent_chats` |
| `src/core/plexijoin/schema.py` | `plexijoin_connections`, `plexijoin_inbound_requests`, `plexijoin_traffic_log` |
| `src/core/dsar/schema.py` | `dsar_requests`, `dsar_export_manifest` |
| `src/core/artifacts/schema.py` | `artifacts`, `voice_calls`, `artifact_ops`, `server_artifact_settings`, `server_artifact_feature_settings` |
| `src/core/suite/stash/schema.py` | `stash_collections`, `stash_items`, `stash_item_text`, `stash_versions`, `stash_grants`, `stash_collection_grants`, `stash_share_links`, `stash_download_tickets` |
| `src/core/suite/sum/schema.py` | `sum_workbooks`, `sum_operations`, `sum_snapshots`, `sum_revision_blobs`, `sum_grants`, `sum_features`, `sum_range_permissions`, `suite_audit_log` |

**Rollback:** The `down(db)` function is destructive and guarded. It drops only tables in `ROLLBACK_TABLE_ALLOWLIST` (`src/core/migrations/migrations/000_initial_schema.py:129`) and never drops migration-tracking tables such as `migrations_history`, `migration_logs`, or `migration_lock`. The rollback refuses to run unless you set `MIGRATIONS_ALLOW_DESTRUCTIVE_ROLLBACK=1` in the environment. On PostgreSQL it uses `DROP TABLE ... CASCADE`; on SQLite it orders tables children-first to satisfy foreign keys. Use the rollback only on disposable databases.

### 001 — Self-test no-op

**Purpose:** Verify that the migration apply and rollback endpoints work correctly during self-tests.

**Impact:** Low — No database changes.

**Changes:** None. The migration is a no-op.

**Considerations:** The self-test system uses this migration to verify that the migration manager, runner, and tracker handle apply and rollback correctly without affecting real schema.

## Schema module files

All table definitions live in schema module files. Each module exposes a `create_tables(db)` function that is idempotent.

| Module | File |
| --- | --- |
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
| Media dedup | `src/core/media/deduplication` |
| Media chunked | `src/core/media/chunked.py` |
| Reports | `src/core/reports/schema.py` |
| Feedback | `src/core/feedback/__init__.py` |
| Avatars | `src/core/avatars/schema.py` |
| Telemetry | `src/core/telemetry/__init__.py` |
| Search | `src/core/search/schema.py` |
| AutoMod | `src/core/automod/schema.py` |
| Admin | `src/core/admin/schema.py` |
| DM spam | `src/core/dmspam/schema.py` |
| Chat tracking | `src/core/chat_tracking/schema.py` |
| PlexiJoin | `src/core/plexijoin/schema.py` |
| DSAR | `src/core/dsar/schema.py` |
| Artifacts | `src/core/artifacts/schema.py` |
| Suite stash | `src/core/suite/stash/schema.py` |
| Suite sum | `src/core/suite/sum/schema.py` |

## SQLite versus PostgreSQL

Schema files use `db.convert_schema()` to handle dialect differences:

*   `INTEGER PRIMARY KEY AUTOINCREMENT` becomes `BIGINT PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY` on PostgreSQL.
*   `INTEGER` becomes `BIGINT`.
*   `BLOB` becomes `BYTEA`.
*   `BOOLEAN DEFAULT 0` or `1` becomes `BOOLEAN DEFAULT FALSE` or `TRUE`.

## Check migration status

1.  Open the admin panel at `/api/v1/admin/ui-migrations`.
2.  Review the migration list and check which migrations are applied, pending, or failed.
3.  Select **Details** on any migration to see logs and metadata.
