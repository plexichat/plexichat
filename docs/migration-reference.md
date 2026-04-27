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
| 015 | Expand API Access Tokens | Reversible | Low | Expands API token capabilities |
| 016 | Add Account Deletion Support | Reversible | High | Adds account deletion with grace period |
| 017 | Harden Admin 2FA | Reversible | High | Additional 2FA hardening for admins |
| 018 | Feature Expansion | Reversible | Medium | Expands feature support |
| 019 | Fix Last Chat Bigint Columns | Reversible | Low | Fixes last chat column types |
| 020 | Fix Last Chat SQLite Bigint | Reversible | Low | SQLite-specific bigint fix |
| 021 | Add Encryption Config | Reversible | Low | Adds encryption configuration support |
| 022 | Add Poll Encrypted Columns | Reversible | Low | Adds encrypted columns for polls |
| 023 | Add Description Encrypted Columns | Reversible | Low | Adds encrypted columns for descriptions |
| 024 | Migrate Poll Encrypted Data | Reversible | Medium | Migrates poll data to encrypted format |
| 025 | Drop Poll Unencrypted Columns | **Irreversible** | High | Drops unencrypted poll columns |
| 026 | Add Description Encrypted Columns | Reversible | Low | Adds encrypted columns for descriptions/topics |
| 027 | Migrate Description Encrypted Data | Reversible | Medium | Migrates description data to encrypted format |
| 028 | Drop Description Unencrypted Columns | **Irreversible** | High | Drops unencrypted description/topic columns |
| 029 | Add Internal Notes Encrypted Columns | Reversible | Low | Adds encrypted columns for internal notes |
| 030 | Migrate Internal Notes Encrypted Data | Reversible | Medium | Migrates internal notes to encrypted format |
| 031 | Drop Internal Notes Unencrypted Columns | **Irreversible** | High | Drops unencrypted internal notes columns |

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

### 015 - Expand API Access Tokens

**Purpose**: Expands API token capabilities and permissions.

**Impact**: Low - Feature enhancement.

**Changes**: Adds additional fields to API tokens.

**Considerations**: Enhances API token functionality.

---

### 016 - Add Account Deletion Support

**Purpose**: Adds account deletion with 30-day grace period.

**Impact**: High - Feature addition.

**Changes**: Adds deletion status and timestamp to user accounts.

**Considerations**: Important for GDPR compliance and user privacy.

---

### 017 - Harden Admin 2FA

**Purpose**: Additional 2FA hardening for administrator accounts.

**Impact**: High - Security enhancement.

**Changes**: Strengthens 2FA requirements for admins.

**Considerations**: Critical for admin account security.

---

### 018 - Feature Expansion

**Purpose**: Expands feature support across the platform.

**Impact**: Medium - Feature addition.

**Changes**: Multiple feature enhancements.

**Considerations**: Enables new platform features.

---

### 019 - Fix Last Chat Bigint Columns

**Purpose**: Fixes last chat column types to use proper bigint.

**Impact**: Low - Schema fix.

**Changes**: Modifies last chat columns to bigint type.

**Considerations**: Resolves potential overflow issues with large IDs.

---

### 020 - Fix Last Chat SQLite Bigint

**Purpose**: SQLite-specific fix for last chat bigint columns.

**Impact**: Low - Schema fix.

**Changes**: SQLite-specific bigint fix.

**Considerations**: Only applies to SQLite databases.

---

### 021 - Add Encryption Config

**Purpose**: Adds encryption configuration support to the database.

**Impact**: Low - Configuration addition.

**Changes**: Adds encryption configuration columns.

**Considerations**: Enables encryption features to be configured.

---

### 022 - Add Poll Encrypted Columns

**Purpose**: Adds encrypted columns for poll questions and options.

**Impact**: Low - Feature addition.

**Changes**: Adds `question_encrypted` and `text_encrypted` columns.

**Considerations**: Part of poll encryption feature. Requires encryption config enabled.

---

### 023 - Add Description Encrypted Columns

**Purpose**: Adds encrypted columns for descriptions and topics.

**Impact**: Low - Feature addition.

**Changes**: Adds encrypted columns for servers, channels, threads, and sticker packs.

**Considerations**: Part of description encryption feature. Requires encryption config enabled.

---

### 024 - Migrate Poll Encrypted Data

**Purpose**: Migrates existing poll data to encrypted format.

**Impact**: Medium - Data migration.

**Changes**: Encrypts all existing poll questions and options.

**Validation**: Decrypts and verifies all encrypted data matches original.

**Dependencies**: 022, 023

**Considerations**: 
- Requires encryption to be configured
- Includes validation to ensure data integrity
- Original data preserved until irreversible migration

---

### 025 - Drop Poll Unencrypted Columns [WARNING]

**Purpose**: Drops unencrypted poll columns after encryption verification.

**Impact**: **High - Irreversible**

**Changes**: 
- Drops `question` column from `poll_polls`
- Drops `text` column from `poll_options`

**Dependencies**: 024

**Delay**: Requires 7 days (configurable) of server uptime since migration 024

**Backup Required**: Yes

**Considerations**:
- **Irreversible** - Cannot be rolled back
- Only run after sufficient verification period
- Ensure encryption is working correctly
- Must have database backup before running
- Use emergency override only in genuine emergencies

**Confirmation Required**: Must type "THE DATABASE IS BACKED UP" to execute

---

### 026 - Add Description Encrypted Columns

**Purpose**: Adds encrypted columns for descriptions and topics (duplicate of 023).

**Impact**: Low - Feature addition.

**Changes**: Adds encrypted columns for descriptions and topics.

**Considerations**: This migration may be redundant with 023.

---

### 027 - Migrate Description Encrypted Data

**Purpose**: Migrates existing description and topic data to encrypted format.

**Impact**: Medium - Data migration.

**Changes**: Encrypts:
- Server descriptions
- Channel topics
- Thread names
- Sticker pack descriptions

**Validation**: Decrypts and verifies all encrypted data matches original.

**Dependencies**: 026

**Considerations**:
- Requires encryption to be configured
- Includes validation for all four data types
- Original data preserved until irreversible migration

---

### 028 - Drop Description Unencrypted Columns [WARNING]

**Purpose**: Drops unencrypted description and topic columns after encryption verification.

**Impact**: **High - Irreversible**

**Changes**:
- Drops `description` column from `srv_servers`
- Drops `topic` column from `srv_channels`
- Drops `name` column from `thread_threads`
- Drops `description` column from `sticker_packs`

**Dependencies**: 027

**Delay**: Requires 7 days (configurable) of server uptime since migration 027

**Backup Required**: Yes

**Considerations**:
- **Irreversible** - Cannot be rolled back
- Only run after sufficient verification period
- Ensure encryption is working correctly for all four data types
- Must have database backup before running
- Use emergency override only in genuine emergencies

**Confirmation Required**: Must type "THE DATABASE IS BACKED UP" to execute

---

### 029 - Add Internal Notes Encrypted Columns

**Purpose**: Adds encrypted columns for internal notes.

**Impact**: Low - Feature addition.

**Changes**: Adds `internal_notes_encrypted` columns to user accounts and feedback.

**Considerations**: Part of internal notes encryption feature.

---

### 030 - Migrate Internal Notes Encrypted Data

**Purpose**: Migrates existing internal notes to encrypted format.

**Impact**: Medium - Data migration.

**Changes**: Encrypts:
- User internal notes
- Feedback internal notes

**Validation**: Decrypts and verifies all encrypted data matches original.

**Dependencies**: 029

**Considerations**:
- Requires encryption to be configured
- Includes validation for both data types
- Original data preserved until irreversible migration

---

### 031 - Drop Internal Notes Unencrypted Columns [WARNING]

**Purpose**: Drops unencrypted internal notes columns after encryption verification.

**Impact**: **High - Irreversible**

**Changes**:
- Drops `internal_notes` column from `auth_users`
- Drops `internal_notes` column from `feedback`

**Dependencies**: 030

**Delay**: Requires 7 days (configurable) of server uptime since migration 030

**Backup Required**: Yes

**Considerations**:
- **Irreversible** - Cannot be rolled back
- Only run after sufficient verification period
- Ensure encryption is working correctly
- Must have database backup before running
- Use emergency override only in genuine emergencies

**Confirmation Required**: Must type "THE DATABASE IS BACKED UP" to execute

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
+-- 015 (Expand API Access Tokens)
+-- 016 (Add Account Deletion Support)
+-- 017 (Harden Admin 2FA)
+-- 018 (Feature Expansion)
+-- 019 (Fix Last Chat Bigint Columns)
+-- 020 (Fix Last Chat SQLite Bigint)
+-- 021 (Add Encryption Config)
|   +-- 022 (Add Poll Encrypted Columns)
|   |   +-- 024 (Migrate Poll Encrypted Data)
|   |       +-- 025 (Drop Poll Unencrypted Columns) [WARNING]
|   +-- 023 (Add Description Encrypted Columns)
|       +-- 026 (Add Description Encrypted Columns)
|           +-- 027 (Migrate Description Encrypted Data)
|               +-- 028 (Drop Description Unencrypted Columns) [WARNING]
+-- 029 (Add Internal Notes Encrypted Columns)
    +-- 030 (Migrate Internal Notes Encrypted Data)
        +-- 031 (Drop Internal Notes Unencrypted Columns) [WARNING]
```

## Risk Levels

- **Low**: Minimal impact, safe to run in production
- **Medium**: Moderate impact, test in staging first
- **High**: Significant impact, requires careful planning and testing

## Special Considerations

### Encryption Migrations (022-031)

These migrations implement data encryption for sensitive fields:

1. **Add Encrypted Columns**: Adds new encrypted columns alongside existing ones
2. **Migrate Data**: Encrypts existing data and validates integrity
3. **Drop Unencrypted Columns**: Removes original columns (irreversible)

**Important**: Never skip the validation step. The data migrations include automatic validation that decrypts and verifies all encrypted data matches the original.

### Irreversible Migrations (025, 028, 031)

These migrations drop unencrypted columns after encryption is verified:

- **Backup Required**: Always have a recent database backup
- **Uptime Delay**: Wait for the configured delay period (default 7 days)
- **Confirmation**: Must type "THE DATABASE IS BACKED UP"
- **Emergency Override**: Use only in genuine emergencies
- **Admin Panel**: Run through the admin panel for safety

### SQLite vs PostgreSQL

Some migrations have SQLite-specific handling:

- Migration 020: SQLite-specific bigint fix
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
