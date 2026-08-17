"""PlexiStash metadata schema and domain shapes.

PlexiStash stores organization and authorization metadata here. File bytes stay
in Plexichat's media subsystem and are referenced by ``media_object_id``.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.database.core.schema_splitter import split_sql_statements


STASH_ITEM_TYPES = ("file", "link", "note", "shortcut")
STASH_SCOPES = ("personal", "server")
STASH_PERMISSIONS = (
    "view_metadata",
    "preview",
    "download",
    "edit",
    "move",
    "share",
    "delete",
    "manage_versions",
)

# Permissions a personal-item owner may delegate to another user. Privileged
# mutations (``share``, ``delete``, ``manage_versions``) are excluded: letting a
# grantee delete or reshare the owner's personal item is an IDOR/privilege
# escalation. Server-scoped items keep the full set (governed by the server
# permission boundary instead).
PERSONAL_ITEM_GRANTABLE_PERMISSIONS = (
    "view_metadata",
    "preview",
    "download",
    "edit",
    "move",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stash_items (
    id BIGINT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    item_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    scope TEXT NOT NULL,
    server_id BIGINT,
    collection_id BIGINT,
    media_object_id TEXT,
    current_version_id BIGINT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    CHECK (item_type IN ('file', 'link', 'note', 'shortcut')),
    CHECK (scope IN ('personal', 'server')),
    CHECK ((scope = 'personal' AND server_id IS NULL) OR (scope = 'server' AND server_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_stash_items_owner ON stash_items(owner_id, deleted, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_stash_items_server ON stash_items(server_id, deleted, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_stash_items_collection ON stash_items(collection_id, deleted);
CREATE INDEX IF NOT EXISTS idx_stash_items_pinned ON stash_items(owner_id, pinned, deleted);

CREATE TABLE IF NOT EXISTS stash_collections (
    id BIGINT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    server_id BIGINT,
    -- Nested collections: a child collection may point at a parent folder.
    parent_id BIGINT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES stash_collections(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_stash_collections_owner ON stash_collections(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_stash_collections_server ON stash_collections(server_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_stash_collections_parent ON stash_collections(parent_id);

-- Opaque, optionally password-protected read-only share links. Only the
-- SHA-256 of the token (and the Argon2id hash of the password) is persisted:
-- the plaintext token is returned exactly once and is never recoverable.
CREATE TABLE IF NOT EXISTS stash_share_links (
    id BIGINT PRIMARY KEY,
    item_id BIGINT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    expires_at BIGINT,
    revoked_at BIGINT,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_by BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES stash_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stash_share_links_item ON stash_share_links(item_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_stash_share_links_expiry ON stash_share_links(expires_at, revoked_at);

-- Grants on a collection, inherited by every item inside it (and by items
-- inside its descendant collections). Sharing a folder once is equivalent to
-- sharing each contained item, without denormalizing per-item grants.
CREATE TABLE IF NOT EXISTS stash_collection_grants (
    id BIGINT PRIMARY KEY,
    collection_id BIGINT NOT NULL,
    subject_id BIGINT NOT NULL,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    expires_at BIGINT,
    revoked_at BIGINT,
    created_by BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(collection_id, subject_id),
    FOREIGN KEY (collection_id) REFERENCES stash_collections(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stash_collection_grants_collection
    ON stash_collection_grants(collection_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_stash_collection_grants_subject
    ON stash_collection_grants(subject_id, revoked_at);

-- Extracted plain text from a stash item's media object, used for full-text
-- search. Binary content that fails UTF-8 decoding is intentionally absent,
-- so a search never matches undecodable bytes.
CREATE TABLE IF NOT EXISTS stash_item_text (
    item_id BIGINT PRIMARY KEY,
    content_text TEXT,
    updated_at BIGINT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES stash_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stash_item_text_updated ON stash_item_text(updated_at);

CREATE TABLE IF NOT EXISTS stash_versions (
    id BIGINT PRIMARY KEY,
    item_id BIGINT NOT NULL,
    created_by BIGINT NOT NULL,
    media_object_id TEXT,
    content_type TEXT,
    size_bytes BIGINT,
    checksum TEXT,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES stash_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stash_versions_item ON stash_versions(item_id, created_at DESC);

CREATE TABLE IF NOT EXISTS stash_grants (
    id BIGINT PRIMARY KEY,
    item_id BIGINT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    expires_at BIGINT,
    revoked_at BIGINT,
    created_by BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(item_id, subject_type, subject_id),
    FOREIGN KEY (item_id) REFERENCES stash_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stash_grants_item ON stash_grants(item_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_stash_grants_subject ON stash_grants(subject_type, subject_id, revoked_at);

CREATE TABLE IF NOT EXISTS stash_download_tickets (
    id BIGINT PRIMARY KEY,
    item_id BIGINT NOT NULL,
    version_id BIGINT,
    subject_id BIGINT NOT NULL,
    -- When non-NULL the ticket was issued to a share-link recipient; the
    -- redemption path re-validates this share link instead of the normal
    -- item-grant/server authorization.
    share_link_id BIGINT,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at BIGINT NOT NULL,
    redeemed_at BIGINT,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES stash_items(id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES stash_versions(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_stash_download_tickets_expiry
    ON stash_download_tickets(expires_at, redeemed_at);
CREATE INDEX IF NOT EXISTS idx_stash_download_tickets_subject
    ON stash_download_tickets(subject_id, expires_at);

CREATE TABLE IF NOT EXISTS suite_audit_log (
    id BIGINT PRIMARY KEY,
    domain TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id BIGINT,
    actor_id BIGINT NOT NULL,
    details_json TEXT,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suite_audit_resource
    ON suite_audit_log(domain, resource_type, resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_suite_audit_actor
    ON suite_audit_log(actor_id, created_at DESC);
"""


def create_tables(db) -> None:
    """Create Stash metadata tables for SQLite or PostgreSQL."""
    for statement in split_sql_statements(SCHEMA):
        if statement.strip():
            converted = (
                db.convert_schema(statement)
                if hasattr(db, "convert_schema")
                else statement
            )
            db.execute(converted)


@dataclass(frozen=True)
class StashItem:
    """Drive-like item metadata referencing an existing storage object."""

    item_id: str
    owner_id: int
    item_type: str
    title: str
    description: Optional[str]
    scope: str
    server_id: Optional[int]
    collection_id: Optional[str]
    media_object_id: Optional[str]
    current_version_id: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    pinned: bool = False
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class StashVersion:
    version_id: str
    item_id: str
    created_by: int
    media_object_id: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    checksum: Optional[str]
    created_at: int


@dataclass(frozen=True)
class StashGrant:
    grant_id: str
    item_id: str
    subject_type: str
    subject_id: Optional[str]
    permissions: List[str]
    expires_at: Optional[int] = None
    revoked_at: Optional[int] = None
    inherited_from: Optional[str] = None


@dataclass(frozen=True)
class StashAccessContext:
    user_id: int
    item_id: str
    requested_permission: str
    server_id: Optional[int] = None
    grant_id: Optional[str] = None
    now_ms: Optional[int] = None
