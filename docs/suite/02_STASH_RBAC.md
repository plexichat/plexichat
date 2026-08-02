# PlexiStash: Drive-like storage and RBAC

## Positioning

PlexiStash is a Google Drive-like personal/team vault for files, links, notes,
collections, versions, and shared resources. It is not merely another artifact
filter or upload list.

The client owns the Drive-like experience. Plexichat owns identity, media
storage, authorization primitives, notifications, audit, and administration.

## Storage boundary

Reuse the existing Plexichat media/storage layer for binary bytes. Stash adds
metadata and organization:

- item identity and type
- collection hierarchy
- tags and favorites
- version metadata
- previews and searchable metadata
- explicit grants and links
- security audit history

Do not create a second binary storage implementation. Do not expose permanent
storage URLs. Use opaque IDs and short-lived download tickets.

## Logical entities

```text
stash_items
  item_id, owner_id, item_type, title, description, scope,
  server_id, collection_id, media_object_id, current_version_id, metadata

stash_collections
  collection_id, owner_id, scope, server_id, parent_collection_id, name

stash_item_versions
  version_id, item_id, created_by, media_object_id, content_type,
  size_bytes, checksum, created_at

stash_item_tags
  item_id, tag_id

stash_grants
  grant_id, item_id/collection_id, subject_type, subject_id,
  permissions, expires_at, revoked_at, inherited_from

stash_audit_events
  event_id, actor_id, item_id, action, target, metadata, created_at
```

These are logical contracts, not an approved migration. IDs should use the
project's safe opaque identifier convention and must not be confused with
filesystem paths.

## Item types

- file: existing media object
- link: normalized URL plus title/description metadata
- note: editable text with version history
- shortcut: reference to another permitted Stash item or artifact

## Scopes

- personal: owner-only by default
- server: server/team container with configured inherited access
- direct grant: explicit user or role grant
- shared link: expiring/revocable external-style access

Server membership must not automatically make every personal item visible.
Collection inheritance must be explicit, testable, and auditable.

## Permission model

Separate permissions are required for:

- view metadata
- preview
- download
- edit
- move
- share
- delete/trash
- manage versions

Seeing a filename does not imply permission to download it. Every endpoint must
re-resolve the item and evaluate the requested action on the server.

## Mandatory security controls

1. Authenticate and reject invalid/suspended users.
2. Resolve opaque item IDs; never accept client filesystem paths.
3. Apply ownership, collection inheritance, explicit grants, role grants, and
   revocations in a defined order.
4. Validate scope and server membership.
5. Validate link expiration and revocation.
6. Issue short-lived, scope-checked download tickets.
7. Recheck authorization when a ticket is consumed.
8. Sniff/validate content types and enforce size limits.
9. Normalize filenames and block path traversal.
10. Treat HTML/SVG, archives, and previews as hostile content.
11. Isolate preview generation where required.
12. Audit upload, preview, download, share, revoke, move, version restore,
    delete, and administrative access.
13. Keep trash/unlink/version deletion/physical media deletion distinct.
14. Ensure DSAR export and erasure cover Stash metadata, grants, versions,
    audit references, and underlying media according to retention policy.

## Client screens

The Stash home includes My Files, Recent, Shared with Me, Shared by Me,
Favorites, Collections, Links, Notes, tags, search, storage usage, and upload
status. The workspace includes breadcrumbs, list/grid view, safe preview,
details, versions, sharing, activity, and a return-to-chat action.
