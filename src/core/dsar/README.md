# DSAR module

Export personal data for GDPR Article 20 (Right to Data Portability) compliance.

## Overview

The DSAR (Data Subject Access Request) module lets users request a complete export of their personal data in a structured, machine-readable format. The module supports JSON and ZIP exports, an optional admin approval workflow, background processing, and a tamper-evident audit log.

Use this module when you need to fulfill a user's request to receive the personal data they provided to PlexiChat.

## Features

* **Multiple export formats** — Single JSON file or ZIP archive with one file per category.
* **Comprehensive data collection** — Covers 20+ data categories across 40+ tables.
* **Admin approval workflow** — Optionally require admin review before generation.
* **Background processing** — `DSARHarvester` processes requests asynchronously.
* **Audit trail** — Hash-chained, append-only log at `~/.plexichat/data/dsar_audit_log.jsonl`.
* **Storage-layer encryption** — No envelope encryption in the DSAR module itself; exports are encrypted at rest only when `media.encrypt_at_rest` is enabled.
* **Automatic expiration** — Downloads expire after the configured retention period.

## Data categories

The export includes the following categories. Each category maps to one or more database tables.

| Category | Tables |
|----------|--------|
| Identity | `auth_users` (passwords and secrets excluded) |
| Sessions | `auth_sessions`, `auth_devices`, `auth_known_ips` |
| Profile | `user_profiles`, `user_settings`, `msg_content_filters`, `pres_custom_status` |
| Messages | `msg_messages`, `msg_participants`, `msg_conversations`, `msg_forwarded`, `msg_scheduled`, `msg_edit_history`, `user_bookmarks` |
| Relationships | `rel_friends`, `rel_friend_requests`, `rel_blocked` |
| Servers | `srv_members`, `srv_onboarding_progress` |
| Content | `msg_pinned`, `react_reactions`, `msg_attachments` (metadata only) |
| Notifications | `notif_notifications`, `notif_unread`, `notif_settings`, `notif_channel_overrides` |
| OAuth | `auth_external_accounts` |
| Applications | `app_applications`, `app_installations`, `app_oauth_tokens` |
| Reports | `message_reports`, `user_reports` |
| Feedback | `feedback` |
| Search | `search_history`, `saved_searches` |
| Features | `user_features`, `user_feature_usage`, `user_features_audit` |
| Polls | `poll_votes`, `poll_polls` |
| Voice | `voice_states` |
| Automod | `automod_violations`, `automod_reputation`, `automod_exemptions` |
| Presence | `pres_presence`, `pres_typing` |
| Stickers | `sticker_usage` |
| Soundboard | `soundboard_usage` |
| Media | `media_files` (metadata only) |
| Avatars | `user_avatars` (metadata only; `avatar_data` BLOB not exported) |
| API tokens | `auth_api_access_tokens` (secret values excluded) |

For the domain-specific breakdown used by the collector layer, see `collectors/README.md`.

## Setup

Initialize the module once at application startup:

```python
from src.core.database import Database
from src.core.dsar import setup

db = Database()
db.connect()
setup(db)
```

`setup()` creates the DSAR tables and initializes `DSARManager` and `DSARHarvester`. Call `is_setup()` to check whether initialization has completed.

## Usage

### Request an export

```python
from src.core.dsar import request_data_export, get_request_status, get_export_file

# Request an export in JSON format
request = request_data_export(user_id=12345, format="json")

# Poll the status
status = get_request_status(request["id"], user_id=12345)
print(status["status"])  # pending, approved, generating, ready, downloaded, expired, etc.

# Download when ready
if status["status"] == "ready":
    export_file = get_export_file(request["id"], user_id=12345)
    print(export_file["file_path"])
    print(export_file["checksum"])
```

### Manage requests as an admin

```python
from src.core.dsar import get_admin_requests, approve_request, deny_request, generate_manual

# List pending requests
requests = get_admin_requests(status="pending")

# Approve or deny
approve_request(request_id=123, admin_id=1)
deny_request(request_id=123, admin_id=1, reason="Additional verification needed")

# Trigger generation manually
generate_manual(request_id=123, admin_id=1)
```

### Manage requests as a user

```python
from src.core.dsar import get_user_requests, cancel_request, mark_export_downloaded

# List your requests
requests = get_user_requests(user_id=12345)

# Cancel a pending request
cancel_request(request_id=123, user_id=12345)

# Mark as downloaded after streaming the file to the client
mark_export_downloaded(request_id=123, user_id=12345)
```

## Configuration

Configure the module in `config.json`:

```json
{
  "dsar": {
    "enabled": true,
    "interval_hours": 1,
    "batch_size": 10,
    "require_admin_review": true,
    "retention_days": 7,
    "max_export_size_mb": 100,
    "export_formats": ["json", "zip"],
    "audit_log": {
      "file_path": "~/.plexichat/data/dsar_audit_log.jsonl",
      "hash_chain_enabled": true,
      "halt_on_invalid_audit": true
    },
    "harvester": {
      "enabled": true,
      "interval_hours": 1,
      "batch_size": 10,
      "require_admin_review": true,
      "retention_days": 7
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `enabled` | Enable the DSAR module. |
| `interval_hours` | How often the harvester checks for work. |
| `batch_size` | Maximum requests processed per harvester tick. |
| `require_admin_review` | Whether exports require admin approval before generation. |
| `retention_days` | Days before a ready export expires. |
| `max_export_size_mb` | Maximum export file size. |
| `export_formats` | Allowed formats (`json`, `zip`). |

## Architecture

```
dsar/
├── __init__.py        # Public API and module setup
├── schema.py          # Database table definitions
├── models.py          # Data classes (DSARRequest, DSARStatus)
├── audit_log.py       # Hash-chained append-only audit log (DSARLog)
├── collector.py       # DataCollector: orchestrates domain collectors
├── collectors/        # Domain-specific collectors (one per data category)
├── export_formats.py  # ExportFormatGenerator: JSON and ZIP generation
├── harvester.py       # DSARHarvester: background worker thread
├── manager.py         # DSARManager: high-level request lifecycle
└── README.md          # This file
```

| Component | File | Responsibility |
|-----------|------|----------------|
| `DSARManager` | `manager.py` | Request lifecycle: create, approve, deny, cancel, authorize, and cache invalidation. |
| `DSARHarvester` | `harvester.py` | Background thread that cleans up expired requests and drives pending/approved requests through `DataCollector` and `ExportFormatGenerator`. |
| `DataCollector` | `collector.py` | Delegates to domain collectors in `collectors/` and aggregates results. Excludes sensitive fields. Returns plain dictionaries for JSON serialization. |
| `ExportFormatGenerator` | `export_formats.py` | Generates JSON (single file) or ZIP (one file per category) and stores files through the media storage backend (default `~/.plexichat/data/exports/dsar/`). No envelope encryption; relies on `media.encrypt_at_rest` when enabled. |
| `DSARLog` | `audit_log.py` | Append-only, hash-chained log of actions: `REQUESTED`, `APPROVED`, `DENIED`, `GENERATING`, `READY`, `DOWNLOADED`, `EXPIRED`, `FAILED`, `CANCELLED`. Verifies integrity on startup. |

## Request status flow

```
pending → approved → generating → ready → downloaded
  │          │                      │
  │          └────→ cancelled      └────→ expired
  │
  └────→ denied ──→ failed
```

| Status | Description |
|--------|-------------|
| `pending` | Awaiting admin review (when `require_admin_review` is true) or harvester pickup. |
| `approved` | Approved by an admin; queued for generation. |
| `generating` | Data collection and file generation in progress. |
| `ready` | Export file is available for download. |
| `downloaded` | User has downloaded the file. |
| `expired` | Retention period elapsed; file removed. |
| `denied` | Rejected by an admin. |
| `cancelled` | Cancelled by the user. |
| `failed` | Generation failed. |

## Security considerations

1. **No built-in envelope encryption** — Enable `media.encrypt_at_rest` to encrypt export files at rest.
2. **Passwords and secrets excluded** — Password hashes, token secrets, and `avatar_data` BLOBs are never exported.
3. **Token redaction** — API tokens and OAuth tokens are exported without secret values.
4. **Encrypted fields** — Encrypted column values are represented as `(encrypted)` rather than exposing ciphertext.
5. **Authorization** — Users can access only their own requests; admin operations require an admin ID.
6. **Expiration** — Download links expire after `retention_days`.
7. **Audit trail** — Every state transition is recorded in the hash-chained audit log.

## Compliance

This module supports GDPR Article 20 — Right to Data Portability:

> The data subject shall have the right to receive the personal data concerning him or her, which he or she has provided to a controller, in a structured, commonly used and machine-readable format and have the right to transmit those data to another controller without hindrance.

For the right to erasure (Article 17, Right to be Forgotten), see the `AccountReaper` in the `auth` module. Where `AccountReaper` deletes data, DSAR exports it.

## API reference

All public functions are re-exported from `src.core.dsar` (`__init__.py:87`):

| Function | Description |
|----------|-------------|
| `setup(db)` | Initialize the module. |
| `is_setup()` | Return whether the module is initialized. |
| `request_data_export(user_id, format, categories)` | Create a new export request. |
| `get_request_status(request_id, user_id)` | Get the status of a request. |
| `get_user_requests(user_id)` | List all requests for a user. |
| `get_export_file(request_id, user_id)` | Get file metadata for a ready export. |
| `mark_export_downloaded(request_id, user_id)` | Mark an export as downloaded after streaming. |
| `cancel_request(request_id, user_id)` | Cancel a pending request. |
| `get_admin_requests(status, limit, offset)` | List requests with optional status filter. |
| `approve_request(request_id, admin_id)` | Approve a pending request. |
| `deny_request(request_id, admin_id, reason)` | Deny a pending request. |
| `generate_manual(request_id, admin_id)` | Trigger generation manually. |
