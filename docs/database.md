# Database Reference

This document describes the Plexichat database schema, with a focus on
encrypted columns and column-level encryption.

## Encrypted Columns Reference

Plexichat encrypts sensitive data at the column level using AES-256-GCM
(via the system keyring). Some columns have a paired `*_encrypted` mirror
that is the preferred write target; the original column is kept for
backwards-compatible reads. New writes populate the encrypted mirror;
reads prefer `*_encrypted` when present and fall back to the legacy
column.

### High-sensitivity columns

| Table | Column |
|-------|--------|
| `msg_messages` | `content` (may be `ENC:v1:...` blob) |
| `msg_edit_history` | `old_content_encrypted` |
| `msg_attachments` | `url_encrypted` |
| `srv_servers` | `description_encrypted` |
| `srv_channels` | `topic_encrypted` |
| `thread_threads` | `name_encrypted` |
| `sticker_packs` | `description_encrypted` |
| `auth_users` | `email_encrypted`, `totp_secret_encrypted` |
| `auth_user_notes` | `note_encrypted` |
| `auth_sessions` | `ip_encrypted`, `ua_encrypted` |
| `auth_known_ips` | `ip_encrypted` |
| `auth_ip_blacklist` | `ip_encrypted` |
| `auth_audit_log` | `details_encrypted` |
| `auth_2fa_challenges` | `ip_encrypted`, `ua_encrypted` |
| `auth_api_access_tokens` | `token_encrypted`, `last_used_ip_encrypted`, `ua_encrypted` |
| `auth_api_access_token_events` | `ip_encrypted`, `ua_encrypted` |

### Medium-sensitivity columns (paired `*_encrypted` mirror)

| Table | Column | Migration |
|-------|--------|-----------|
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

`app_interactions.data` is also encrypted at write time (via
`encrypt_data`) since the interaction handler rollout; legacy rows
written before the rollout remain as plain JSON and are read via
`json.loads` for backward compatibility.
