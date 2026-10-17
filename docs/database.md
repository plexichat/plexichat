# Database reference

This document describes the Plexichat database schema, with a focus on
column-level encryption.

Plexichat encrypts sensitive data at the column level with AES-256-GCM.
Decryption uses the system keyring. Some columns have a paired
`*_encrypted` mirror that is the preferred write target. The original
column is kept for backward-compatible reads. New writes populate the
encrypted mirror. Reads prefer `*_encrypted` when present and fall back
to the legacy column.

## Encrypted columns

### High-sensitivity columns

These columns always contain sensitive data and are written as encrypted
values. Some use a blind index column (for example, `email_index`,
`ip_index`) to support lookups without revealing plaintext.

| Table | Encrypted column | Notes |
|-------|------------------|-------|
| `msg_messages` | `content` | Stores plain text or an `ENC:v1:...` envelope. `content_encrypted` is a legacy mirror; prefer `content`. |
| `msg_edit_history` | `old_content_encrypted` | Previous content for an edit. |
| `msg_attachments` | `url_encrypted` | Storage URL for the attachment. |
| `srv_servers` | `description_encrypted` | Server description. |
| `srv_channels` | `topic_encrypted` | Channel topic. |
| `srv_bans` | `reason_encrypted` | Ban reason. Plain `reason` is kept for compatibility. |
| `thread_threads` | `name_encrypted` | Thread name. |
| `sticker_packs` | `description_encrypted` | Pack description. |
| `auth_users` | `email_encrypted` | Paired with blind index `email_index`. |
| `auth_users` | `totp_secret_encrypted` | TOTP secret. |
| `auth_users` | `display_name_encrypted` | Display name. |
| `auth_users` | `internal_notes_encrypted` | Internal admin notes. |
| `auth_users` | `reason_encrypted` | Login or ban reason. |
| `auth_user_notes` | `note_encrypted` | Private note about another user. |
| `auth_sessions` | `ip_encrypted`, `ua_encrypted` | Paired with `ip_index` and `ua_index`. |
| `auth_known_ips` | `ip_encrypted` | Paired with `ip_index`. |
| `auth_ip_blacklist` | `ip_encrypted` | Paired with `ip_index`. |
| `auth_audit_log` | `details_encrypted`, `ip_encrypted` | Audit details and IP. |
| `auth_2fa_challenges` | `ip_encrypted`, `ua_encrypted` | Paired with `ip_index` and `ua_index`. |
| `auth_api_access_tokens` | `token_encrypted`, `last_used_ip_encrypted`, `ua_encrypted` | Token is paired with `token_index`. |
| `auth_api_access_token_events` | `ip_encrypted`, `ua_encrypted` | Event IP and user agent. |
| `auth_bots` | `display_name_encrypted` | Bot display name. |

`app_interactions.data` is also encrypted at write time through
`encrypt_data`. Rows written before that rollout remain as plain JSON and
are read with `json.loads` for backward compatibility.

### Medium-sensitivity columns with an `*_encrypted` mirror

These columns were added with an encrypted mirror in migrations 040 and
041. New code writes the encrypted mirror. Reads prefer the mirror and
fall back to the plain column.

| Table | Encrypted column | Migration |
|-------|------------------|-----------|
| `auth_devices` | `name_encrypted` | 041 |
| `auth_devices` | `device_type_encrypted` | 041 |
| `auth_devices` | `fingerprint_encrypted` | 041 |
| `auth_external_accounts` | `external_id_encrypted` | 041 |
| `auth_passkeys` | `device_name_encrypted` | 041 |
| `notif_notifications` | `content_preview_encrypted` | 041 |
| `srv_audit_log` | `changes_encrypted` | 041 |
| `user_settings` | `value_encrypted` | 041 |
| `app_webhook_deliveries` | `request_body_encrypted` | 040 |
| `app_webhook_deliveries` | `response_body_encrypted` | 040 |

## Schema source of truth

The tables in this document are a summary. The authoritative DDL is in the
schema modules:

* `src/core/auth/schema.py`
* `src/core/messaging/schema.py`
* `src/core/servers/schema.py`
* `src/core/notifications/schema.py`
* `src/core/media/schema.py`

If you add an encrypted column, update this document and the corresponding
schema module.

## Related documentation

* [Default configuration reference](default-config.md)
* [Database configuration](deployment/configuration/config-database.md)
