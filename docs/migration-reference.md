# Migration Reference

This document provides a detailed reference for all database migrations in Plexichat, including their purpose, impact, and any special considerations for system administrators.

## Migration Index

| Version | Name | Type | Risk Level | Description |
|---------|------|------|------------|-------------|
| 000 | Initial Schema | Reversible | High | Creates the base database schema |
| 001 | Initial Example | Reversible | Low | Example migration for testing |
| 002 | Legacy Transfer | Reversible | Medium | Transfers legacy data |
| 003 | Add TOTP Secret Encrypted | Reversible | Medium | Adds encrypted TOTP secret column |
| 004 | Fix User Settings PK | Reversible | Low | Fixes user settings primary key |
| 005 | Consolidated Schema Fixes | Reversible | Medium | Multiple schema fixes consolidated |
| 006 | Add Message Status Index | Reversible | Low | Adds index for message status |
| 007 | Add Username Blacklist | Reversible | Low | Adds username blacklist table |
| 008 | Harden Admin Security | Reversible | High | Security hardening for admin accounts |
| 009 | Add DB Metrics to Telemetry | Reversible | Low | Adds database metrics to telemetry |
| 010 | Add Internal Notes to Auth Users | Reversible | Low | Adds internal notes column to users |
| 011 | Add Content Index to Messages | Reversible | Medium | Adds content index for message search |
| 012 | Add Applied Roles to AutoMod | Reversible | Low | Adds applied roles tracking for auto-mod |
| 013 | Add Timeout to Server Members | Reversible | Low | Adds timeout tracking for server members |
| 014 | Add API Access Tokens | Reversible | Medium | Adds API access token support |
| 015 | Add Max Reactions Per Message | Reversible | Low | Adds max reactions per message server setting |
| 016 | Expand API Access Tokens | Reversible | Low | Expands API token capabilities and permissions |
| 017 | Add Account Deletion Support | Reversible | High | Adds account deletion with grace period |
| 018 | Harden Admin 2FA | Reversible | High | Hardens admin 2FA with encrypted secrets and backup codes |
| 019 | Feature Expansion | Reversible | Medium | Expands feature support (profiles, threads, bookmarks, etc.) |
| 020 | Fix Last Chat Bigint Columns | Reversible | Low | Fixes last chat column types to use proper bigint |
| 021 | Fix Last Chat SQLite Bigint | Reversible | Low | SQLite-specific bigint fix for last chat |
| 022 | Add Encryption Config | Reversible | Low | Adds encryption configuration support |
| 023 | Fix Keyring Mismatch Auth | Reversible | Medium | Fixes admin lockout after keyring/KEK mismatch |
| 024 | Add Poll Encrypted Columns | Reversible | Low | Adds encrypted columns for poll questions and options |
| 025 | Migrate Poll Encrypted Data | Reversible | Medium | Migrates existing poll data to encrypted format |
| 026 | Drop Poll Unencrypted Columns | **Irreversible** | High | Drops unencrypted poll columns |
| 027 | Add Description Encrypted Columns | Reversible | Low | Adds encrypted columns for descriptions and topics |
| 028 | Migrate Description Encrypted Data | Reversible | Medium | Migrates description data to encrypted format |
| 029 | Drop Description Unencrypted Columns | **Irreversible** | High | Drops unencrypted description/topic columns |
| 030 | Add Internal Notes Encrypted Columns | Reversible | Low | Adds encrypted columns for internal notes |
| 031 | Migrate Internal Notes Encrypted Data | Reversible | Medium | Migrates internal notes to encrypted format |
| 032 | Drop Internal Notes Unencrypted Columns | **Irreversible** | High | Drops unencrypted internal notes columns |
| 033 | Admin RBAC System | Reversible | Medium | Implements role-based access control for admin panel |
| 034 | Admin Notes Versioning | Reversible | Low | Adds versioning and markdown support to admin notes |
| 035 | PlexiJoin Federation System | Reversible | Medium | Adds PlexiJoin federation tables for server linking |
| 036 | Fix Thread Threads Columns | Reversible | Low | Fixes thread_threads schema with missing columns |
| 037 | Selftest Noop | Reversible | Low | No-op migration for self-test system verification |
| 038 | Add Sticker Stickers Description Column | Reversible | Low | Adds description column to sticker_stickers table |
| 039 | Add Admin Role Position | Reversible | Medium | Adds position column to admin_roles for hierarchy |
| 040 | Add Webhook Delivery Encrypted Columns | Reversible | Low | Adds encrypted columns for webhook delivery payloads |
| 041 | Add Medium Sensitivity Encrypted Columns | Reversible | Low | Adds encrypted columns for medium-sensitivity data |
| 042 | Add Low Sensitivity Encrypted Columns | Reversible | Low | Adds encrypted columns for low-sensitivity operator-visible data |
| 043 | Add Search Index Dirty Tracking | Reversible | Low | Adds incremental reindex tracking for search |
| 044 | Add DSAR Tables | Reversible | Medium | Adds DSAR tables for GDPR compliance |
| 045 | Add Channel Ratchet | Reversible | Medium | Adds channel ratchet intervals for message encryption |
| 046 | SB Cooldown Zero To Null Grace | Reversible | Medium | Migrates soundboard cooldown 0 to NULL for new semantics |

## Detailed Migration Descriptions

### 000 - Initial Schema

**Purpose**: Creates the base database schema for Plexichat.

**Impact**: High - Creates all core tables.

**Tables Created**:
- User authentication tables
- Messaging tables
- Server management tables
- And other core system tables

**Considerations**: This is the foundation migration that all others depend on.

---

### 001 - Initial Example

**Purpose**: Example migration for testing the migration system.

**Impact**: Low - Minimal impact.

**Considerations**: Used for testing and validation of the migration system.

---

### 002 - Legacy Transfer

**Purpose**: Transfers data from legacy systems to the new schema.

**Impact**: Medium - Data migration.

**Considerations**: Only applicable when migrating from a legacy system.

---

### 003 - Add TOTP Secret Encrypted

**Purpose**: Adds encrypted TOTP secret column for two-factor authentication.

**Impact**: Medium - Security enhancement.

**Changes**: Adds `totp_secret_encrypted` column to user accounts.

**Considerations**: Part of 2FA implementation. Data is encrypted at rest.

---

### 004 - Fix User Settings PK

**Purpose**: Fixes primary key issue in user settings table.

**Impact**: Low - Schema fix.

**Changes**: Modifies user settings table primary key.

**Considerations**: Resolves a schema inconsistency.

---

### 005 - Consolidated Schema Fixes

**Purpose**: Applies multiple schema fixes in a single migration.

**Impact**: Medium - Multiple schema changes.

**Changes**: Various schema corrections and optimizations.

**Considerations**: Consolidates several fixes for efficiency.

---

### 006 - Add Message Status Index

**Purpose**: Adds an index on message status for improved query performance.

**Impact**: Low - Performance improvement.

**Changes**: Creates index on message status column.

**Considerations**: Improves performance of message status queries.

---

### 007 - Add Username Blacklist

**Purpose**: Adds username blacklist table to prevent certain usernames.

**Impact**: Low - Feature addition.

**Changes**: Creates username blacklist table.

**Considerations**: Used for moderation and preventing offensive usernames.

---

### 008 - Harden Admin Security

**Purpose**: Security hardening for administrator accounts.

**Impact**: High - Security enhancement.

**Changes**: Multiple security improvements for admin accounts.

**Considerations**: Important for production security. Review changes carefully.

---

### 009 - Add DB Metrics to Telemetry

**Purpose**: Adds database metrics to telemetry system.

**Impact**: Low - Monitoring enhancement.

**Changes**: Adds database performance metrics to telemetry.

**Considerations**: Helps with monitoring and performance analysis.

---

### 010 - Add Internal Notes to Auth Users

**Purpose**: Adds internal notes column to user accounts for admin use.

**Impact**: Low - Feature addition.

**Changes**: Adds `internal_notes` column to auth_users table.

**Considerations**: Used by administrators to track user-related notes.

---

### 011 - Add Content Index to Messages

**Purpose**: Adds content index for improved message search performance.

**Impact**: Medium - Performance improvement.

**Changes**: Creates index on message content.

**Considerations**: Significantly improves message search performance.

---

### 012 - Add Applied Roles to AutoMod

**Purpose**: Adds tracking for roles applied by auto-moderation.

**Impact**: Low - Feature addition.

**Changes**: Adds applied roles tracking to auto-mod system.

**Considerations**: Helps audit auto-mod actions.

---

### 013 - Add Timeout to Server Members

**Purpose**: Adds timeout tracking for server members.

**Impact**: Low - Feature addition.

**Changes**: Adds timeout columns to server members table.

**Considerations**: Used for temporary timeouts/bans.

---

### 014 - Add API Access Tokens

**Purpose**: Adds API access token support for external integrations.

**Impact**: Medium - Feature addition.

**Changes**: Creates API access tokens table.

**Considerations**: Enables external API access to Plexichat.

---

### 015 - Add Max Reactions Per Message

**Purpose**: Adds server-level maximum reactions per message setting.

**Impact**: Low - Feature addition.

**Changes**: Adds `max_reactions_per_message` column to `srv_servers` table.

**Considerations**: Allows server admins to configure reaction limits.

---

### 016 - Expand API Access Tokens

**Purpose**: Expands API access token capabilities and permissions.

**Impact**: Low - Feature enhancement.

**Changes**: Adds additional fields and permission scopes to API tokens.

**Considerations**: Enhances API token functionality.

---

### 017 - Add Account Deletion Support

**Purpose**: Adds account deletion with grace period.

**Impact**: High - Feature addition.

**Changes**: Adds deletion status and timestamp to user accounts.

**Considerations**: Important for GDPR compliance and user privacy.

---

### 018 - Harden Admin 2FA

**Purpose**: Hardens admin 2FA with encrypted secrets and backup codes.

**Impact**: High - Security enhancement.

**Changes**:
- Adds encrypted TOTP secret column
- Adds hashed backup codes column
- Adds OTP challenge tracking columns
- Migrates existing plaintext secrets to encrypted storage

**Considerations**: Critical for admin account security. Includes safe rollback via backup table.

---

### 019 - Feature Expansion

**Purpose**: Expands feature support across the platform.

**Impact**: Medium - Feature addition.

**Changes**:
- DM threaded conversations
- Voice message support
- Message forwarding
- Scheduled messages
- User bookmarks
- User profiles with custom status
- Report flow enhancements
- Thread slowmode
- DM anti-spam
- Webhook retry queue
- Push notification tokens
- Last chat tracking

**Considerations**: Enables new platform features.

---

### 020 - Fix Last Chat Bigint Columns

**Purpose**: Fixes last chat column types to use proper bigint.

**Impact**: Low - Schema fix.

**Changes**: Modifies last chat columns to bigint type.

**Considerations**: Resolves potential overflow issues with large IDs.

---

### 021 - Fix Last Chat SQLite Bigint

**Purpose**: SQLite-specific fix for last chat bigint columns.

**Impact**: Low - Schema fix.

**Changes**: SQLite-specific bigint fix.

**Considerations**: Only applies to SQLite databases.

---

### 022 - Add Encryption Config

**Purpose**: Adds encryption configuration support to the database.

**Impact**: Low - Configuration addition.

**Changes**: Adds encryption configuration columns.

**Considerations**: Enables encryption features to be configured.

---

### 023 - Fix Keyring Mismatch Auth

**Purpose**: Fixes admin lockout after keyring/KEK mismatch or rotation.

**Impact**: Medium - Security/availability fix.

**Changes**: Resets admin 2FA state when encrypted data cannot be decrypted due to keyring mismatch.

**Considerations**: Fixes scenario where admins are locked out after key rotation or corruption.

---

### 024 - Add Poll Encrypted Columns

**Purpose**: Adds encrypted columns for poll questions and options.

**Impact**: Low - Feature addition.

**Changes**: Adds `question_encrypted` and `text_encrypted` columns to poll tables.

**Considerations**: Part of poll encryption feature. Requires encryption config enabled.

---

### 025 - Migrate Poll Encrypted Data

**Purpose**: Migrates existing poll data to encrypted format.

**Impact**: Medium - Data migration.

**Changes**: Encrypts all existing poll questions and options.

**Validation**: Decrypts and verifies all encrypted data matches original.

**Dependencies**: 022, 024

**Considerations**:
- Requires encryption to be configured
- Includes validation to ensure data integrity
- Original data preserved until irreversible migration

---

### 026 - Drop Poll Unencrypted Columns [WARNING]

**Purpose**: Drops unencrypted poll columns after encryption verification.

**Impact**: **High - Irreversible**

**Changes**:
- Drops `question` column from `poll_polls`
- Drops `text` column from `poll_options`

**Dependencies**: 025

**Delay**: Requires 7 days (configurable) of server uptime since migration 025

**Backup Required**: Yes

**Considerations**:
- **Irreversible** - Cannot be rolled back
- Only run after sufficient verification period
- Ensure encryption is working correctly
- Must have database backup before running
- Use emergency override only in genuine emergencies

**Confirmation Required**: Must type "THE DATABASE IS BACKED UP" to execute

---

### 027 - Add Description Encrypted Columns

**Purpose**: Adds encrypted columns for descriptions and topics.

**Impact**: Low - Feature addition.

**Changes**: Adds encrypted columns for servers, channels, threads, and sticker packs.

**Considerations**: Part of description encryption feature. Requires encryption config enabled.

---

### 028 - Migrate Description Encrypted Data

**Purpose**: Migrates existing description and topic data to encrypted format.

**Impact**: Medium - Data migration.

**Changes**: Encrypts:
- Server descriptions
- Channel topics
- Thread names
- Sticker pack descriptions

**Validation**: Decrypts and verifies all encrypted data matches original.

**Dependencies**: 027

**Considerations**:
- Requires encryption to be configured
- Includes validation for all four data types
- Original data preserved until irreversible migration

---

### 029 - Drop Description Unencrypted Columns [WARNING]

**Purpose**: Drops unencrypted description and topic columns after encryption verification.

**Impact**: **High - Irreversible**

**Changes**:
- Drops `description` column from `srv_servers`
- Drops `topic` column from `srv_channels`
- Drops `name` column from `thread_threads`
- Drops `description` column from `sticker_packs`

**Dependencies**: 028

**Delay**: Requires 7 days (configurable) of server uptime since migration 028

**Backup Required**: Yes

**Considerations**:
- **Irreversible** - Cannot be rolled back
- Only run after sufficient verification period
- Ensure encryption is working correctly for all four data types
- Must have database backup before running
- Use emergency override only in genuine emergencies

**Confirmation Required**: Must type "THE DATABASE IS BACKED UP" to execute

---

### 030 - Add Internal Notes Encrypted Columns

**Purpose**: Adds encrypted columns for internal notes.

**Impact**: Low - Feature addition.

**Changes**: Adds `internal_notes_encrypted` columns to user accounts and feedback.

**Considerations**: Part of internal notes encryption feature.

---

### 031 - Migrate Internal Notes Encrypted Data

**Purpose**: Migrates existing internal notes to encrypted format.

**Impact**: Medium - Data migration.

**Changes**: Encrypts:
- User internal notes
- Feedback internal notes

**Validation**: Decrypts and verifies all encrypted data matches original.

**Dependencies**: 030

**Considerations**:
- Requires encryption to be configured
- Includes validation for both data types
- Original data preserved until irreversible migration

---

### 032 - Drop Internal Notes Unencrypted Columns [WARNING]

**Purpose**: Drops unencrypted internal notes columns after encryption verification.

**Impact**: **High - Irreversible**

**Changes**:
- Drops `internal_notes` column from `auth_users`
- Drops `internal_notes` column from `feedback`

**Dependencies**: 031

**Delay**: Requires 7 days (configurable) of server uptime since migration 031

**Backup Required**: Yes

**Considerations**:
- **Irreversible** - Cannot be rolled back
- Only run after sufficient verification period
- Ensure encryption is working correctly
- Must have database backup before running
- Use emergency override only in genuine emergencies

**Confirmation Required**: Must type "THE DATABASE IS BACKED UP" to execute

---

### 033 - Admin RBAC System

**Purpose**: Implements role-based access control for the admin panel.

**Impact**: Medium - Feature addition.

**Changes**:
- Creates `admin_roles` table for role definitions with permissions
- Creates `admin_role_assignments` table for role assignments
- Creates `admin_audit_log` table for comprehensive audit logging
- Creates `admin_approvals` table for approval workflows
- Adds `force_password_change` column to `admin_users`
- Enhanced admin security features

**Considerations**: Introduces granular admin permission management.

---

### 034 - Admin Notes Versioning

**Purpose**: Adds versioning and markdown support to admin notes.

**Impact**: Low - Feature addition.

**Changes**:
- Creates `admin_notes_versioning` table for tracking note changes
- Adds markdown support flag
- Adds version history for admin notes

**Considerations**: Enables tracking of admin note edits.

---

### 035 - PlexiJoin Federation System

**Purpose**: Adds PlexiJoin federation tables for server linking.

**Impact**: Medium - Feature addition.

**Changes**:
- Creates `plexijoin_connections` for outbound federation links
- Creates `plexijoin_inbound_requests` for incoming join requests
- Creates `plexijoin_traffic_log` for message traffic counters
- Adds indexes for admin audit query performance

**Considerations**: Enables cross-server federation.

---

### 036 - Fix Thread Threads Columns

**Purpose**: Fixes thread_threads schema with missing columns.

**Impact**: Low - Schema fix.

**Changes**: Adds missing columns to `thread_threads`:
- `name`, `name_encrypted`
- `slowmode_interval_ms`
- `slowmode_updated_by`
- `slowmode_updated_at`

Also fixes the slowmode column name mismatch introduced in migration 019.

**Considerations**: Aligns schema with the actual code expectations.

---

### 037 - Selftest Noop

**Purpose**: No-op migration for self-test system verification.

**Impact**: Low - No database changes.

**Changes**: None. This migration is a no-op.

**Considerations**: Designed purely for the self-test system to verify that apply and rollback operations work correctly.

---

### 038 - Add Sticker Stickers Description Column

**Purpose**: Adds description column to the sticker_stickers table.

**Impact**: Low - Schema fix.

**Changes**: Adds `description` column to `sticker_stickers` if missing.

**Dependencies**: 037

**Considerations**: Aligns sticker schema with current code expectations.

---

### 039 - Add Admin Role Position

**Purpose**: Adds position column to admin_roles for hierarchy enforcement.

**Impact**: Medium - Feature addition.

**Changes**: Adds `position` column to `admin_roles` with default values based on role type.

**Considerations**: Enables lower-ranked admins to be prevented from modifying higher-ranked ones.

---

### 040 - Add Webhook Delivery Encrypted Columns

**Purpose**: Adds encrypted columns for webhook delivery payloads.

**Impact**: Low - Feature addition.

**Changes**: Adds `request_body_encrypted` and `response_body_encrypted` to `app_webhook_deliveries`.

**Dependencies**: 039

**Considerations**: Original columns kept for backwards-compatible reads. New writes go to encrypted columns.

---

### 041 - Add Medium Sensitivity Encrypted Columns

**Purpose**: Adds encrypted columns for medium-sensitivity data.

**Impact**: Low - Feature addition.

**Changes**: Adds paired `*_encrypted` columns to:
- `auth_devices`: name, device_type, fingerprint
- `auth_external_accounts`: external_id
- `auth_passkeys`: device_name
- `notif_notifications`: content_preview
- `srv_audit_log`: changes
- `user_settings`: value

**Dependencies**: 040

**Considerations**: Original plaintext columns kept for backwards-compatible reads.

---

### 042 - Add Low Sensitivity Encrypted Columns

**Purpose**: Adds encrypted columns for low-sensitivity operator-visible data.

**Impact**: Low - Feature addition.

**Changes**: Adds paired `*_encrypted` columns to:
- `auth_bots`: display_name
- `srv_bans`: reason
- `auth_ip_blacklist`: reason

**Dependencies**: 041

**Considerations**: Defense-in-depth at-rest protection. Original columns retained.

---

### 043 - Add Search Index Dirty Tracking

**Purpose**: Adds incremental reindex tracking for search.

**Impact**: Low - Performance improvement.

**Changes**: Adds `source_updated_at` to `search_message_index`.

**Dependencies**: 042

**Considerations**: Enables O(dirty) reindex instead of O(all). Existing rows default to 0 (treat as dirty on first reindex).

---

### 044 - Add DSAR Tables

**Purpose**: Adds DSAR (Data Subject Access Request) tables for GDPR compliance.

**Impact**: Medium - Feature addition.

**Changes**: Creates:
- `dsar_requests` table to track data access requests
- `dsar_export_manifest` table to track exported data per table

**Dependencies**: 043

**Considerations**: Supports GDPR data subject access request workflows.

---

### 045 - Add Channel Ratchet

**Purpose**: Adds channel ratchet intervals for the v3 message ratchet.

**Impact**: Medium - Feature addition.

**Changes**: Creates `channel_ratchet_intervals` table and adds `ratchet_interval_id` to `msg_messages`.

**Dependencies**: 044

**Considerations**: Supports per-channel encryption key rotation.

---

### 046 - SB Cooldown Zero To Null Grace

**Purpose**: Migrates soundboard cooldown semantics from 0 to NULL.

**Impact**: Medium - Data migration.

**Changes**: Converts `cooldown_seconds = 0` to `NULL` in `soundboard_sounds`. Adds grace config key for rollout safety.

**Dependencies**: 045

**Considerations**:
- Affects sound cooldown behavior unchanged-by-owner sounds
- Owners who explicitly set cooldown=0 must re-issue after migration
- Trade-off documented in `tech_spec.md` and release notes

---

## Migration Dependencies

Some migrations depend on others being applied first. The dependency chain is:

```
000 (Initial Schema)
+-- 001 (Initial Example)
+-- 002 (Legacy Transfer)
+-- 003 (Add TOTP Secret Encrypted)
+-- 004 (Fix User Settings PK)
+-- 005 (Consolidated Schema Fixes)
+-- 006 (Add Message Status Index)
+-- 007 (Add Username Blacklist)
+-- 008 (Harden Admin Security)
+-- 009 (Add DB Metrics to Telemetry)
+-- 010 (Add Internal Notes to Auth Users)
+-- 011 (Add Content Index to Messages)
+-- 012 (Add Applied Roles to AutoMod)
+-- 013 (Add Timeout to Server Members)
+-- 014 (Add API Access Tokens)
+-- 015 (Add Max Reactions Per Message)
+-- 016 (Expand API Access Tokens)
+-- 017 (Add Account Deletion Support)
+-- 018 (Harden Admin 2FA)
+-- 019 (Feature Expansion)
+-- 020 (Fix Last Chat Bigint Columns)
+-- 021 (Fix Last Chat SQLite Bigint)
+-- 022 (Add Encryption Config)
|   +-- 024 (Add Poll Encrypted Columns)
|   |   +-- 025 (Migrate Poll Encrypted Data)
|   |       +-- 026 (Drop Poll Unencrypted Columns) [WARNING]
|   +-- 027 (Add Description Encrypted Columns)
|   |   +-- 028 (Migrate Description Encrypted Data)
|   |       +-- 029 (Drop Description Unencrypted Columns) [WARNING]
|   +-- 030 (Add Internal Notes Encrypted Columns)
|       +-- 031 (Migrate Internal Notes Encrypted Data)
|           +-- 032 (Drop Internal Notes Unencrypted Columns) [WARNING]
+-- 033 (Admin RBAC System)
|   +-- 039 (Add Admin Role Position)
|       +-- 040 (Add Webhook Delivery Encrypted Columns)
|           +-- 041 (Add Medium Sensitivity Encrypted Columns)
|               +-- 042 (Add Low Sensitivity Encrypted Columns)
|                   +-- 043 (Add Search Index Dirty Tracking)
|                       +-- 044 (Add DSAR Tables)
|                           +-- 045 (Add Channel Ratchet)
|                               +-- 046 (SB Cooldown Zero To Null Grace)
+-- 034 (Admin Notes Versioning)
+-- 035 (PlexiJoin Federation System)
|   +-- 036 (Fix Thread Threads Columns)
|       +-- 037 (Selftest Noop)
|           +-- 038 (Add Sticker Stickers Description Column)
```

## Risk Levels

- **Low**: Minimal impact, safe to run in production
- **Medium**: Moderate impact, test in staging first
- **High**: Significant impact, requires careful planning and testing

## Special Considerations

### Encryption Migrations (024-032)

These migrations implement data encryption for sensitive fields:

1. **Add Encrypted Columns**: Adds new encrypted columns alongside existing ones
2. **Migrate Data**: Encrypts existing data and validates integrity
3. **Drop Unencrypted Columns**: Removes original columns (irreversible)

**Important**: Never skip the validation step. The data migrations include automatic validation that decrypts and verifies all encrypted data matches the original.

### Irreversible Migrations (026, 029, 032)

These migrations drop unencrypted columns after encryption is verified:

- **Backup Required**: Always have a recent database backup
- **Uptime Delay**: Wait for the configured delay period (default 7 days)
- **Confirmation**: Must type "THE DATABASE IS BACKED UP"
- **Emergency Override**: Use only in genuine emergencies
- **Admin Panel**: Run through the admin panel for safety

### SQLite vs PostgreSQL

Some migrations have SQLite-specific handling:

- Migration 021: SQLite-specific bigint fix
- Column drops in SQLite require table recreation
- PostgreSQL supports direct column drops

## Checking Migration Status

To check which migrations have been applied:

1. Navigate to the admin panel at `/api/v1/admin/ui-migrations`
2. View the migration list showing:
   - Applied migrations (green status)
   - Pending migrations (yellow status)
   - Failed migrations (red status)
3. Click "Details" on any migration to see logs and metadata

## Getting Help

If you encounter issues with a migration:

1. Check the migration logs in the admin panel
2. Review the specific migration description in this document
3. Consult the main [Migrations Guide](migrations.md)
4. Check server logs for additional error details
5. Contact Plexichat support with the migration version and error message
