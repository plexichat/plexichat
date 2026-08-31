# PlexiStash

Metadata storage and authorization service for PlexiStash — a Drive-like file organization system. Stores organization and authorization metadata; binary content stays in the media subsystem.

## Overview

PlexiStash manages items (files, links, notes, shortcuts), collections (folders), sharing (grants and share links), versioning, download tickets, and full-text search indexing. It enforces a layered authorization model: owner → explicit grants → collection-inherited grants → server membership → server admin. The manager never accepts filesystem paths and never stores file bytes — it references opaque media object IDs resolved through the media boundary.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      StashManager                         │
│  ┌─────────────────────────┬────────────────────────────┐ │
│  │ StashManagerCollections │   StashManagerItems        │ │
│  │ (folders, share links,  │ (CRUD, versions, grants,   │ │
│  │  text indexing, diffs)  │  download tickets, purge)  │ │
│  ├─────────────────────────┴────────────────────────────┤ │
│  │               StashManagerBase                       │ │
│  │  (auth, audit, media resolution, permission checks)  │ │
│  ├──────────────────────────────────────────────────────┤ │
│  │                 BaseManager                          │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Database    │  │ Media Module │  │ Servers Module│  │
│  │  (SQLite/PG) │  │ (file bytes) │  │ (membership)  │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Authorization Evaluation Order

1. Authenticate the user and reject suspended/invalid sessions.
2. Resolve the opaque item ID; never accept a filesystem path.
3. Check item/collection ownership and explicit grants.
4. Resolve server-role grants only when the item is server-scoped.
5. Apply inherited grants and explicit revocations.
6. Validate expiring/shared-link state and the requested permission.
7. Apply deletion/version state.
8. Record security-sensitive actions in the audit stream.

## Key Files

| File | Purpose |
|------|---------|
| `manager.py` | Stable import facade — composes mixins into `StashManager` |
| `stash_manager_base.py` | Construction, audit logging, authorization, media resolution, permission checks |
| `stash_manager_items.py` | Item CRUD, versions, grants, download tickets, purge |
| `stash_manager_collections.py` | Collection CRUD, collection grants, share links, text indexing, version diffs |
| `stash_manager_protocol.py` | Shared member surface for type-checking across mixins |
| `schema.py` | SQL schema, domain dataclasses (`StashItem`, `StashVersion`, `StashGrant`, `StashAccessContext`) |
| `permissions.py` | Permission vocabulary, validation helpers, delegable-permission rules |
| `_shared.py` | Shared exception types (`StashNotFoundError`, `StashPermissionError`, etc.) |
| `cleanup.py` | Background cleanup job for soft-deleted items and expired tickets |

## Usage

### Initialize

```python
from src.core.suite import stash

manager = stash.setup(db, servers_module=servers, media=media)
```

### Create an item

```python
item = manager.create_item(
    user_id=user_id,
    item_type="file",
    title="Design mockup",
    scope="server",
    server_id=server_id,
    collection_id=folder_id,
    media_object_id="12345",
    tags=["design", "ui"],
)
```

### List items

```python
items, total = manager.list_items(
    user_id=user_id,
    scope="personal",
    search="design",
    limit=50,
    offset=0,
)
```

### Share an item

```python
grant = manager.grant(
    user_id=owner_id,
    item_id=item_id,
    subject_id=other_user_id,
    permissions=["view_metadata", "preview", "download"],
    expires_at=future_timestamp,
)
```

### Create a share link

```python
link = manager.create_share_link(
    user_id=owner_id,
    item_id=item_id,
    password="optional-password",
    permissions=["view_metadata", "preview", "download"],
    expires_at=future_timestamp,
)
# link["token"] is returned once, never recoverable
```

### Download with ticket

```python
ticket = manager.create_download_ticket(
    user_id=user_id,
    item_id=item_id,
    ttl_seconds=300,
)
# ticket["ticket"] is a short-lived opaque token
```

### Version management

```python
version = manager.create_version(
    user_id=user_id,
    item_id=item_id,
    media_object_id="67890",
    content_type="application/pdf",
    size_bytes=1024000,
)
versions = manager.list_versions(user_id, item_id)
```

## Database Schema

| Table | Purpose |
|-------|---------|
| `stash_items` | Item metadata (owner, type, scope, tags, collection) |
| `stash_collections` | Folder hierarchy with parent-child nesting |
| `stash_grants` | Per-item permission grants |
| `stash_collection_grants` | Collection-level grants inherited by contained items |
| `stash_versions` | Item version history |
| `stash_share_links` | Opaque, optionally password-protected share links |
| `stash_download_tickets` | Short-lived one-time download tokens |
| `stash_item_text` | Extracted text for full-text search |
| `suite_audit_log` | Security-sensitive action audit trail |

## Configuration

| Config Key | Default | Description |
|------------|---------|-------------|
| `artifacts.plexistash.cleanup_interval_minutes` | 60 | Minutes between cleanup cycles |
| `artifacts.plexistash.retention_days` | 30 | Days to keep soft-deleted items before purge |

## Related Code

- Stash domain init: `src/core/suite/stash/__init__.py`
- Media module: `src/core/media/__init__.py`
- Servers module: `src/core/servers/__init__.py`
- Suite registry: `src/core/suite/registry/`
- Base manager: `src/core/base.py`
- Encryption utilities: `src/utils/encryption/__init__.py`
