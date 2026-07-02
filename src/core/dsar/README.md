# DSAR Module - Data Subject Access Request

GDPR Article 20 Right to Data Portability compliance module for Plexichat.

## Overview

The DSAR module enables users to request and receive a complete export of all their personal data stored in the system. This fulfills the GDPR requirement that individuals have the right to receive their personal data in a structured, commonly used, and machine-readable format.

## Features

- **Multiple Export Formats**: JSON (single file) or ZIP (categorized files)
- **Comprehensive Data Collection**: All user data across 20+ categories
- **Admin Approval Workflow**: Optional review before data is exported
- **Background Processing**: Automatic handling via DSARHarvester worker
- **Audit Trail**: Hash-chained append-only log for compliance verification
- **Envelope Encryption**: Files are encrypted with randomly generated keys
- **Automatic Expiration**: Downloads expire after configurable retention period

## Data Categories

The exported data includes all personal information organized by category:

| Category | Tables |
|----------|--------|
| Identity | auth_users (excluding passwords and secrets) |
| Sessions | auth_sessions, auth_devices, auth_known_ips |
| Profile | user_profiles, user_settings, msg_content_filters, pres_custom_status |
| Messages | msg_messages, msg_participants, msg_conversations, msg_forwarded, msg_scheduled, msg_edit_history, user_bookmarks |
| Relationships | rel_friends, rel_friend_requests, rel_blocked |
| Servers | srv_members, srv_onboarding_progress |
| Content | msg_pinned, react_reactions, msg_attachments (metadata only) |
| Notifications | notif_notifications, notif_unread, notif_settings, notif_channel_overrides |
| OAuth | auth_external_accounts |
| Applications | app_applications, app_installations, app_oauth_tokens |
| Reports | message_reports, user_reports |
| Feedback | feedback |
| Search | search_history, saved_searches |
| Features | user_features, user_feature_usage, user_features_audit |
| Polls | poll_votes, poll_polls |
| Voice | voice_states |
| Automod | automod_violations, automod_reputation, automod_exemptions |
| Presence | pres_presence, pres_typing |
| Stickers | sticker_usage |
| Soundboard | soundboard_usage |
| Media | media_files (metadata only) |
| Avatars | user_avatars (URL reference, not blob data) |
| API Tokens | auth_api_access_tokens (without secret values) |

## Usage

### User Flow

```python
from src.core.dsar import request_data_export, get_request_status, get_export_file

# Request a data export
request = request_data_export(user_id=12345, format='json')

# Check the status periodically
status = get_request_status(request['id'], user_id=12345)
print(f"Status: {status['status']}")  # pending, approved, generating, ready, etc.

# Once ready, download the file
if status['status'] == 'ready':
    export_file = get_export_file(request['id'], user_id=12345)
    print(f"File: {export_file['file_path']}")
    print(f"Checksum: {export_file['checksum']}")
```

### Admin Flow

```python
from src.core.dsar import get_admin_requests, approve_request, deny_request, generate_manual

# List pending requests
requests = get_admin_requests(status='pending')

# Approve a request
approve_request(request_id=123, admin_id=1)

# Or deny with reason
deny_request(request_id=123, admin_id=1, reason="Additional verification needed")

# Manual generation trigger
generate_manual(request_id=123, admin_id=1)
```

### Module Setup

```python
from src.core.dsar import setup
from src.core.database import Database

db = Database()
db.connect()
setup(db)
```

## Configuration

The DSAR module is configured via `config.json`:

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

## Architecture

```
dsar/
├── __init__.py       # Module exports and public API
├── schema.py         # Database tables
├── models.py         # Data classes (DSARRequest, DSARStatus)
├── audit_log.py      # Hash-chained append-only audit log
├── collector.py       # DataCollector - collects all user data
├── export_formats.py # ExportFormatGenerator - JSON/ZIP generation
├── harvester.py      # DSARHarvester - background worker
├── manager.py        # DSARManager - high-level API
└── README.md         # This file
```

### Components

**DSARHarvester** (harvester.py)
- Background thread that runs periodically
- Cleans up expired requests
- Processes pending/approved requests
- Calls DataCollector and ExportFormatGenerator

**DataCollector** (collector.py)
- Collects all user data from 40+ tables
- Organized by category
- Excludes sensitive data (passwords, tokens, encrypted blobs)
- Returns plain dicts suitable for JSON serialization

**ExportFormatGenerator** (export_formats.py)
- Generates JSON exports (single file with all data)
- Generates ZIP exports (separate file per category)
- Uses envelope encryption with random AES-256 keys
- Stores encrypted file to ~/.plexichat/data/exports/dsar/

**DSARLog** (audit_log.py)
- Append-only hash-chained audit log
- Records all DSAR actions: REQUESTED, APPROVED, DENIED, GENERATING, READY, DOWNLOADED, EXPIRED, FAILED, CANCELLED
- Integrity verification on startup
- Located at ~/.plexichat/data/dsar_audit_log.jsonl

**DSARManager** (manager.py)
- High-level API for all DSAR operations
- Request creation, approval, denial, cancellation
- User authorization checks
- Cache invalidation

## Request Status Flow

```
pending → approved → generating → ready → downloaded/expired
    ↓         ↓
  denied    cancelled
    ↓
  failed
```

## Security Considerations

1. **Encrypted Exports**: Files are encrypted with AES-256-GCM
2. **No Passwords Exported**: Password hashes and secrets are excluded
3. **Token Protection**: API tokens and OAuth tokens are exported without secret values
4. **Encrypted Field Handling**: Encrypted fields are noted as "(encrypted)" rather than exposing ciphertext
5. **Automatic Expiration**: Download links expire after configurable period
6. **User Authorization**: Users can only access their own requests
7. **Audit Trail**: All actions are logged to tamper-evident hash chain

## Compliance

This module supports GDPR Article 20 - Right to Data Portability:

> The data subject shall have the right to receive the personal data concerning him or her, which he or she has provided to a controller, in a structured, commonly used and machine-readable format and have the right to transmit those data to another controller without hindrance from the controller to which the personal data have been provided.

## Similar Module

This module is analogous to the `AccountReaper` in the auth module, which handles GDPR Article 17 (Right to Erasure / "Right to be Forgotten"). Where AccountReaper deletes data, DSAR exports it.