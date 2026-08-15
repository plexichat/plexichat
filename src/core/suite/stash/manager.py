"""PlexiStash metadata service.

The service never accepts filesystem paths and never stores file bytes. Binary
content belongs to ``src.core.media``; this layer stores opaque media object
references plus organization and authorization metadata.
"""

import hashlib
import json
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from src.core.base import BaseManager
from .schema import (
    PERSONAL_ITEM_GRANTABLE_PERMISSIONS,
    STASH_ITEM_TYPES,
    STASH_PERMISSIONS,
)


class StashNotFoundError(LookupError):
    """Raised when an item is absent or not visible to the caller."""


class StashPermissionError(PermissionError):
    """Raised when a caller lacks the requested Stash permission."""


class StashValidationError(ValueError):
    """Raised for malformed Stash metadata."""


class StashStorageError(RuntimeError):
    """Raised when a referenced media object cannot be resolved."""


class StashManager(BaseManager):
    """Persistence and authorization boundary for PlexiStash metadata."""

    def __init__(
        self,
        db: Any,
        servers_module: Optional[Any] = None,
        media: Optional[Any] = None,
    ) -> None:
        super().__init__(db)
        self._servers = servers_module
        self._media = media

    def _resolve_media_object(
        self, media_object_id: Optional[str], owner_id: int
    ) -> Optional[Any]:
        """Resolve an active media object and verify it is safe to reference.

        Stash stores only opaque references, but accepting arbitrary strings
        would create dangling records and could later turn into an IDOR. The
        media boundary is authoritative for existence, deleted state, and
        access. Numeric IDs and generated filenames are both supported for
        compatibility with existing media records.
        """
        if media_object_id is None:
            return None
        value = str(media_object_id).strip()
        if (
            not value
            or len(value) > 512
            or any(char in value for char in ("\x00", "\r", "\n"))
        ):
            raise StashValidationError("media_object_id is invalid")
        if self._media is None:
            raise StashStorageError("Media storage is unavailable")
        media_file = None
        if value.isdigit():
            media_file = self._media.get_file(int(value))
        if media_file is None:
            media_file = self._media.get_file_by_filename(value)
        if media_file is None or bool(getattr(media_file, "deleted", False)):
            raise StashValidationError(
                "media_object_id does not identify an active file"
            )
        if value.isdigit():
            media_query = "SELECT deleted FROM media_files WHERE id = ?"
            if getattr(self._db, "type", "sqlite") == "postgres":
                media_query += " FOR UPDATE"
            media_row = self._db.fetch_one(media_query, (int(value),))
            if not media_row or bool(media_row.get("deleted", 0)):
                raise StashStorageError("Media object is unavailable")
            locked = self._db.fetch_one(
                "SELECT 1 FROM media_file_deletion_locks WHERE media_file_id = ?",
                (int(value),),
            )
            if locked:
                raise StashStorageError("Media object is being permanently deleted")
        uploader_id = int(getattr(media_file, "uploaded_by", 0))
        if uploader_id != int(owner_id):
            try:
                allowed = bool(self._media.check_file_access(value, int(owner_id)))
            except Exception:
                allowed = False
            if not allowed:
                raise StashPermissionError("You cannot reference this media object")
        return media_file

    @staticmethod
    def _validate_link_metadata(
        item_type: str, metadata: Optional[Dict[str, Any]]
    ) -> None:
        """Reject unsafe link URLs before they are persisted as metadata.

        Link items store a URL in ``metadata.url``. The client only performs a
        shallow regex check, so the server is the authoritative boundary:
        accept only absolute http(s) URLs so a future link-preview/renderer
        cannot be coerced into fetching ``file:``, ``javascript:``, or
        internal/private endpoints (SSRF / XSS).
        """
        if item_type != "link":
            return
        url = (metadata or {}).get("url")
        if url is None:
            # A link without a URL is tolerated (draft/empty), matching the
            # existing permissive behavior for other metadata.
            return
        if not isinstance(url, str):
            raise StashValidationError("link url must be a string")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise StashValidationError("link url must be an absolute http(s) URL")

    def _audit(
        self,
        action: str,
        resource_type: str,
        resource_id: Optional[int],
        actor_id: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a Suite action without making audit storage a failure point."""
        try:
            self._db.execute(
                "INSERT INTO suite_audit_log "
                "(id, domain, action, resource_type, resource_id, actor_id, details_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    "stash",
                    action,
                    resource_type,
                    resource_id,
                    actor_id,
                    self._json(details, {}) if details else None,
                    int(time.time() * 1000),
                ),
            )
        except Exception:
            # An audit outage must not turn a successful user operation into a
            # misleading 500, but the failure is intentionally observable.
            import utils.logger as logger

            logger.warning("PlexiStash audit write failed", exc_info=True)

    @staticmethod
    def _json(value: Any, fallback: Any) -> str:
        try:
            return json.dumps(
                value if value is not None else fallback, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise StashValidationError("Metadata must be JSON serializable") from exc

    @staticmethod
    def _decode(value: Optional[str], fallback: Any) -> Any:
        if not value:
            return fallback
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return fallback
        return decoded

    @staticmethod
    def _validate_scope(
        scope: str, server_id: Optional[int]
    ) -> Tuple[str, Optional[int]]:
        normalized = str(scope or "personal").strip().lower()
        if normalized not in ("personal", "server"):
            raise StashValidationError("scope must be personal or server")
        if normalized == "server" and server_id is None:
            raise StashValidationError("server_id is required for server-scoped items")
        if normalized == "personal" and server_id is not None:
            raise StashValidationError("personal items cannot include server_id")
        return normalized, int(server_id) if server_id is not None else None

    def _is_member(self, user_id: int, server_id: int) -> bool:
        if self._servers is None:
            return False
        try:
            return self._servers.get_member(server_id, user_id) is not None
        except Exception:
            return False

    def _can_manage_server(self, user_id: int, server_id: int) -> bool:
        """Return whether the caller explicitly manages the server."""
        if self._servers is None:
            return False
        try:
            server = self._servers.get_server(server_id, user_id)
            if server is not None and int(getattr(server, "owner_id", 0)) == int(
                user_id
            ):
                return True
        except Exception:
            pass
        try:
            return bool(
                self._servers.has_permission(user_id, server_id, "server.manage")
            )
        except Exception:
            return False

    def _validate_collection(
        self,
        user_id: int,
        collection_id: Optional[int],
        scope: str,
        server_id: Optional[int],
    ) -> None:
        """Validate collection ownership and scope before attaching an item."""
        if collection_id is None:
            return
        collection = self._db.fetch_one(
            "SELECT owner_id, server_id FROM stash_collections WHERE id = ?",
            (int(collection_id),),
        )
        if not collection:
            raise StashValidationError("collection_id does not identify a collection")
        collection_server = collection.get("server_id")
        if scope == "personal":
            if collection_server is not None or int(collection["owner_id"]) != int(
                user_id
            ):
                raise StashPermissionError(
                    "Collection is not available for this personal item"
                )
            return
        if (
            server_id is None
            or collection_server is None
            or int(collection_server) != int(server_id)
        ):
            raise StashValidationError(
                "Server item collection must belong to the same server"
            )
        if not self._is_member(user_id, int(server_id)):
            raise StashPermissionError("You are not a member of this server")

    def _grant_permissions(self, item_id: int, user_id: int, permission: str) -> bool:
        now = int(time.time() * 1000)
        row = self._db.fetch_one(
            "SELECT permissions_json, expires_at FROM stash_grants "
            "WHERE item_id = ? AND subject_type = 'user' AND subject_id = ? "
            "AND revoked_at IS NULL",
            (item_id, str(user_id)),
        )
        if not row or (
            row.get("expires_at") is not None and int(row["expires_at"]) <= now
        ):
            return False
        permissions = self._decode(row.get("permissions_json"), [])
        if permission in permissions or "*" in permissions:
            return True
        # Being able to mutate an item implies the ability to read its
        # metadata/preview: an editor must be able to view what they edit.
        if permission in ("view_metadata", "preview") and any(
            p in permissions
            for p in ("edit", "manage_versions", "move", "share", "delete")
        ):
            return True
        return False

    def _authorize(
        self,
        item: Dict[str, Any],
        user_id: int,
        permission: str,
        *,
        mutation: bool = False,
    ) -> None:
        if permission not in STASH_PERMISSIONS:
            raise StashValidationError("Unknown Stash permission")
        owner = int(item["owner_id"]) == int(user_id)
        server_id = item.get("server_id")
        if item["scope"] == "personal":
            if owner or self._grant_permissions(int(item["id"]), user_id, permission):
                return
        elif server_id is not None and self._is_member(user_id, int(server_id)):
            if mutation:
                # Edits, shares, and deletes of server-scoped content are
                # governed by the server permission boundary, never by an
                # item grant. Server admins/moderators may perform lifecycle
                # mutations alongside explicit server managers.
                if owner or self._can_administer_server(user_id, int(server_id)):
                    return
            elif permission in ("preview", "download"):
                # Storage exposure is grant-gated even within a server:
                # seeing metadata never implies permission to fetch bytes.
                if (
                    owner
                    or self._can_administer_server(user_id, int(server_id))
                    or self._grant_permissions(int(item["id"]), user_id, permission)
                ):
                    return
            else:
                # Discovery metadata is shared with members by default.
                return
        raise StashPermissionError("Not authorized to access this Stash item")

    def _can_expose_storage(self, row: Dict[str, Any], user_id: int) -> bool:
        """Return whether storage/version references may be returned."""
        if int(row["owner_id"]) == int(user_id):
            return True
        server_id = row.get("server_id")
        if server_id is not None and self._can_administer_server(
            user_id, int(server_id)
        ):
            return True
        # Editors re-point media (update_item -> _resolve_media_object), so
        # they legitimately handle the opaque media reference and may see it.
        return any(
            self._grant_permissions(int(row["id"]), user_id, permission)
            for permission in ("edit", "preview", "download")
        )

    def _row_to_item(
        self, row: Dict[str, Any], *, include_storage: bool = False
    ) -> Dict[str, Any]:
        return {
            "id": str(row["id"]),
            "owner_id": str(row["owner_id"]),
            "item_type": row["item_type"],
            "title": row["title"],
            "description": row.get("description"),
            "scope": row["scope"],
            "server_id": str(row["server_id"])
            if row.get("server_id") is not None
            else None,
            "collection_id": str(row["collection_id"])
            if row.get("collection_id") is not None
            else None,
            "media_object_id": row.get("media_object_id") if include_storage else None,
            "current_version_id": (
                str(row["current_version_id"])
                if include_storage and row.get("current_version_id") is not None
                else None
            ),
            "metadata": self._decode(row.get("metadata_json"), {}),
            "tags": self._decode(row.get("tags_json"), []),
            "pinned": bool(row.get("pinned", 0)),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def create_item(
        self,
        user_id: int,
        *,
        item_type: str,
        title: str,
        scope: str = "personal",
        server_id: Optional[int] = None,
        description: Optional[str] = None,
        collection_id: Optional[int] = None,
        media_object_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        item_type = str(item_type).strip().lower()
        if item_type not in STASH_ITEM_TYPES:
            raise StashValidationError("Unsupported Stash item type")
        title = str(title or "").strip()
        if not title or len(title) > 500:
            raise StashValidationError("title must be between 1 and 500 characters")
        scope, server_id = self._validate_scope(scope, server_id)
        if (
            scope == "server"
            and server_id is not None
            and not self._is_member(user_id, int(server_id))
        ):
            raise StashPermissionError("You are not a member of this server")
        self._validate_collection(user_id, collection_id, scope, server_id)
        self._validate_link_metadata(item_type, metadata)
        self._resolve_media_object(media_object_id, user_id)
        now = int(time.time() * 1000)
        item_id = self._generate_id()
        clean_tags = sorted(
            {str(tag).strip()[:80] for tag in (tags or []) if str(tag).strip()}
        )[:100]
        self._db.execute(
            "INSERT INTO stash_items (id, owner_id, item_type, title, description, scope, server_id, collection_id, media_object_id, metadata_json, tags_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                user_id,
                item_type,
                title,
                description,
                scope,
                server_id,
                collection_id,
                media_object_id,
                self._json(metadata, {}),
                self._json(clean_tags, []),
                now,
                now,
            ),
        )
        self._audit(
            "create", "item", item_id, user_id, {"item_type": item_type, "scope": scope}
        )
        return self.get_item(user_id, item_id)

    def get_item(self, user_id: int, item_id: int) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "view_metadata")
        item = self._row_to_item(
            row, include_storage=self._can_expose_storage(row, user_id)
        )
        # Only the owner (or a server administrator/moderator) may see who an
        # item is shared with, so grants are never leaked to ordinary grantees.
        is_owner = int(row["owner_id"]) == int(user_id)
        server_id = row.get("server_id")
        can_see_grants = is_owner or (
            server_id is not None
            and self._can_administer_server(user_id, int(server_id))
        )
        if can_see_grants:
            item["grants"] = self._item_grants(int(item_id))
        return item

    def _project_item_after_mutation(
        self, user_id: int, item_id: int
    ) -> Dict[str, Any]:
        """Project an item for a user who just passed an edit/restore check.

        Mutations authorize on ``edit`` (or owner/server-admin), which must not
        then require a separate ``view_metadata`` grant to read the result back.
        Grants stay owner/server-admin scoped, matching ``get_item``.
        """
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        item = self._row_to_item(
            row, include_storage=self._can_expose_storage(row, user_id)
        )
        is_owner = int(row["owner_id"]) == int(user_id)
        server_id = row.get("server_id")
        if is_owner or (
            server_id is not None
            and self._can_administer_server(user_id, int(server_id))
        ):
            item["grants"] = self._item_grants(int(item_id))
        return item

    def _item_grants(self, item_id: int) -> List[Dict[str, Any]]:
        """Return the active (non-revoked) grants for an item."""
        rows = self._db.fetch_all(
            "SELECT id, subject_id, permissions_json, expires_at, created_at "
            "FROM stash_grants WHERE item_id = ? AND revoked_at IS NULL "
            "ORDER BY created_at",
            (int(item_id),),
        )
        grants: List[Dict[str, Any]] = []
        for row in rows:
            grants.append(
                {
                    "id": str(row["id"]),
                    "subject_id": str(row["subject_id"]),
                    "permissions": self._decode(row.get("permissions_json"), []),
                    "expires_at": row.get("expires_at"),
                    "created_at": int(row.get("created_at", 0)),
                }
            )
        return grants

    def list_items(
        self,
        user_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        scope: Optional[str] = None,
        server_id: Optional[int] = None,
        pinned: Optional[bool] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        grant_rows = self._db.fetch_all(
            "SELECT item_id, permissions_json FROM stash_grants "
            "WHERE subject_type = 'user' AND subject_id = ? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (str(user_id), int(time.time() * 1000)),
        )
        grant_item_ids = []
        for grant_row in grant_rows:
            permissions = self._decode(grant_row.get("permissions_json"), [])
            if "view_metadata" in permissions or "*" in permissions:
                grant_item_ids.append(int(grant_row["item_id"]))
        # Bound the IN-clause so a user with an unbounded number of grants
        # cannot inflate a listing query into a pathological parameter list.
        # The listing is already capped by limit/offset; this only constrains
        # the grant-derived visibility set.
        grant_item_ids = grant_item_ids[:500]

        clauses = ["deleted = 0"]
        if grant_item_ids:
            placeholders = ", ".join("?" for _ in grant_item_ids)
            clauses.append(
                "(owner_id = ? OR "
                "(scope = 'server' AND server_id IN "
                "(SELECT server_id FROM srv_members WHERE user_id = ?)) OR "
                f"id IN ({placeholders}))"
            )
            params: List[Any] = [user_id, user_id, *grant_item_ids]
        else:
            clauses.append(
                "(owner_id = ? OR "
                "(scope = 'server' AND server_id IN "
                "(SELECT server_id FROM srv_members WHERE user_id = ?)))"
            )
            params = [user_id, user_id]
        if scope:
            # This is a filter, not a create/update payload: "server" simply
            # restricts to server-scoped items visible via the caller's own
            # memberships, so no specific server_id is required here.
            normalized_scope = str(scope).strip().lower()
            if normalized_scope not in ("personal", "server"):
                raise StashValidationError("scope must be personal or server")
            clauses.append("scope = ?")
            params.append(normalized_scope)
        if server_id is not None:
            if not self._is_member(user_id, int(server_id)):
                return [], 0
            clauses.append("server_id = ?")
            params.append(int(server_id))
        if pinned is not None:
            clauses.append("pinned = ?")
            params.append(1 if pinned else 0)
        if search:
            needle = f"%{str(search).strip().lower()}%"
            clauses.append(
                "(LOWER(title) LIKE ? OR LOWER(COALESCE(description, '')) LIKE ? OR LOWER(tags_json) LIKE ?)"
            )
            params.extend([needle, needle, needle])
        where = " AND ".join(clauses)
        count_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS count FROM stash_items WHERE {where}", tuple(params)
        )
        rows = self._db.fetch_all(
            f"SELECT * FROM stash_items WHERE {where} ORDER BY pinned DESC, updated_at DESC LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        )
        total = int(count_row["count"]) if count_row else 0
        return [
            self._row_to_item(
                row, include_storage=self._can_expose_storage(row, user_id)
            )
            for row in rows
        ], total

    def update_item(self, user_id: int, item_id: int, **fields: Any) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "edit", mutation=True)
        updates: Dict[str, Any] = {}
        if "title" in fields:
            title = str(fields["title"] or "").strip()
            if not title or len(title) > 500:
                raise StashValidationError("title must be between 1 and 500 characters")
            updates["title"] = title
        if "description" in fields:
            updates["description"] = fields["description"]
        if "collection_id" in fields:
            collection_id = fields["collection_id"]
            self._validate_collection(
                user_id,
                int(collection_id) if collection_id is not None else None,
                row["scope"],
                int(row["server_id"]) if row.get("server_id") is not None else None,
            )
            updates["collection_id"] = collection_id
        if "media_object_id" in fields:
            media_object_id = fields["media_object_id"]
            # The person attaching the media is the editor; resolve access
            # against them (not the original owner) so a server manager who
            # uploads a new file can attach it without being wrongly rejected.
            self._resolve_media_object(media_object_id, user_id)
            updates["media_object_id"] = media_object_id
        if "metadata" in fields:
            self._validate_link_metadata(row["item_type"], fields["metadata"])
            updates["metadata_json"] = self._json(fields["metadata"], {})
        if "tags" in fields:
            clean_tags = sorted(
                {
                    str(tag).strip()[:80]
                    for tag in (fields["tags"] or [])
                    if str(tag).strip()
                }
            )[:100]
            updates["tags_json"] = self._json(clean_tags, [])
        if updates:
            now = int(time.time() * 1000)
            assignments = ", ".join(f"{key} = ?" for key in updates)
            self._db.execute(
                f"UPDATE stash_items SET {assignments}, updated_at = ? WHERE id = ?",
                tuple(updates.values()) + (now, int(item_id)),
            )
        self._audit(
            "update",
            "item",
            item_id,
            user_id,
            {"item_type": row["item_type"], "scope": row["scope"]},
        )
        return self._project_item_after_mutation(user_id, item_id)

    def _can_administer_server(self, user_id: int, server_id: int) -> bool:
        """Return whether the caller may perform lifecycle actions on a server.

        A server manager always qualifies; server admins/moderators also
        qualify so moderation/restore is consistent with the create path
        (which only requires membership).
        """
        if self._servers is None:
            return False
        if self._can_manage_server(user_id, server_id):
            return True
        for permission in ("server.admin", "server.moderator"):
            try:
                if self._servers.has_permission(user_id, server_id, permission):
                    return True
            except Exception:
                pass
        return False

    def set_pinned(self, user_id: int, item_id: int, pinned: bool) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "edit", mutation=True)
        self._db.execute(
            "UPDATE stash_items SET pinned = ?, updated_at = ? WHERE id = ?",
            (1 if pinned else 0, int(time.time() * 1000), int(item_id)),
        )
        self._audit("edit", "item", int(item_id), user_id, {"pinned": bool(pinned)})
        return self._project_item_after_mutation(user_id, item_id)

    def delete_item(self, user_id: int, item_id: int) -> None:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "delete", mutation=True)
        self._db.execute(
            "UPDATE stash_items SET deleted = 1, updated_at = ? WHERE id = ?",
            (int(time.time() * 1000), int(item_id)),
        )
        self._audit("delete", "item", int(item_id), user_id)

    def restore_item(self, user_id: int, item_id: int) -> Dict[str, Any]:
        """Restore a soft-deleted item for its owner or server manager."""
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 1", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Deleted Stash item not found")
        # Restore is a lifecycle-management action, not a delegated item
        # permission. A personal item can only be restored by its owner; a
        # server item can also be restored by an explicit server manager.
        owner = int(row["owner_id"]) == int(user_id)
        server_id = row.get("server_id")
        server_manager = (
            server_id is not None
            and self._is_member(user_id, int(server_id))
            and self._can_administer_server(user_id, int(server_id))
        )
        if not owner and not server_manager:
            raise StashPermissionError(
                "Only the owner or a server manager can restore this item"
            )
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE stash_items SET deleted = 0, updated_at = ? WHERE id = ?",
            (now, int(item_id)),
        )
        self._audit("restore", "item", int(item_id), user_id)
        return self.get_item(user_id, item_id)

    def create_version(
        self, user_id: int, item_id: int, **fields: Any
    ) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "manage_versions", mutation=True)
        media_object_id = fields.get("media_object_id")
        self._resolve_media_object(media_object_id, user_id)
        version_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.execute(
            "INSERT INTO stash_versions (id, item_id, created_by, media_object_id, content_type, size_bytes, checksum, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                item_id,
                user_id,
                media_object_id,
                fields.get("content_type"),
                fields.get("size_bytes"),
                fields.get("checksum"),
                now,
            ),
        )
        self._db.execute(
            "UPDATE stash_items SET current_version_id = ?, media_object_id = COALESCE(?, media_object_id), updated_at = ? WHERE id = ?",
            (version_id, media_object_id, now, item_id),
        )
        self._audit("edit", "version", version_id, user_id, {"item_id": int(item_id)})
        return self.get_version(user_id, item_id, version_id)

    def list_versions(
        self, user_id: int, item_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "view_metadata")
        rows = self._db.fetch_all(
            "SELECT * FROM stash_versions WHERE item_id = ? ORDER BY created_at DESC LIMIT ?",
            (item_id, max(1, min(int(limit), 100))),
        )
        include_storage = self._can_expose_storage(item, user_id)
        return [
            self._version_dict(row, include_storage=include_storage) for row in rows
        ]

    def get_version(
        self, user_id: int, item_id: int, version_id: int
    ) -> Dict[str, Any]:
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "view_metadata")
        row = self._db.fetch_one(
            "SELECT * FROM stash_versions WHERE id = ? AND item_id = ?",
            (int(version_id), int(item_id)),
        )
        if not row:
            raise StashNotFoundError("Stash version not found")
        return self._version_dict(
            row, include_storage=self._can_expose_storage(item, user_id)
        )

    @staticmethod
    def _version_dict(
        row: Dict[str, Any], *, include_storage: bool = False
    ) -> Dict[str, Any]:
        return {
            "id": str(row["id"]),
            "item_id": str(row["item_id"]),
            "created_by": str(row["created_by"]),
            "media_object_id": row.get("media_object_id") if include_storage else None,
            "content_type": row.get("content_type") if include_storage else None,
            "size_bytes": row.get("size_bytes") if include_storage else None,
            "checksum": row.get("checksum") if include_storage else None,
            "created_at": int(row["created_at"]),
        }

    def grant(
        self,
        user_id: int,
        item_id: int,
        subject_id: int,
        permissions: List[str],
        expires_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "share", mutation=True)
        clean = sorted(set(permissions))
        if not clean or any(
            permission not in STASH_PERMISSIONS for permission in clean
        ):
            raise StashValidationError("permissions contains an unknown value")
        # A personal item's owner may only delegate non-privileged permissions.
        # Granting ``share``/``delete``/``manage_versions`` to a third party
        # would let them delete or reshare the owner's item (IDOR / privilege
        # escalation); those stay owned by the item owner.
        if row["scope"] == "personal":
            disallowed = set(clean) & (
                set(STASH_PERMISSIONS) - set(PERSONAL_ITEM_GRANTABLE_PERMISSIONS)
            )
            if disallowed:
                raise StashValidationError(
                    "Personal items cannot delegate privileged permissions: "
                    + ", ".join(sorted(disallowed))
                )
        subject = self._db.fetch_one(
            "SELECT id FROM auth_users WHERE id = ?", (int(subject_id),)
        )
        if not subject:
            raise StashValidationError("subject_id does not identify a user")
        if row["scope"] == "server" and row.get("server_id") is not None:
            if not self._is_member(int(subject_id), int(row["server_id"])):
                raise StashPermissionError(
                    "Grant subject is not a member of the item server"
                )
        grant_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.upsert(
            "stash_grants",
            [
                "id",
                "item_id",
                "subject_type",
                "subject_id",
                "permissions_json",
                "expires_at",
                "revoked_at",
                "created_by",
                "created_at",
            ],
            (
                grant_id,
                item_id,
                "user",
                str(subject_id),
                self._json(clean, []),
                expires_at,
                None,
                user_id,
                now,
            ),
            ["item_id", "subject_type", "subject_id"],
            ["permissions_json", "expires_at", "revoked_at"],
        )
        # On re-grant the upsert keeps the pre-existing row's id; the freshly
        # generated ``grant_id`` is never persisted in that case. Return the
        # authoritative row so a subsequent ``revoke_grant`` targets the real
        # grant instead of a phantom id (which previously 404'd).
        persisted = self._db.fetch_one(
            "SELECT id, permissions_json, expires_at FROM stash_grants "
            "WHERE item_id = ? AND subject_type = 'user' AND subject_id = ?",
            (int(item_id), str(subject_id)),
        )
        persisted_id = int(persisted["id"]) if persisted else grant_id
        persisted_permissions = (
            self._decode(persisted.get("permissions_json"), clean)
            if persisted
            else clean
        )
        persisted_expiry = persisted.get("expires_at") if persisted else expires_at
        self._audit(
            "share",
            "item",
            int(item_id),
            user_id,
            {"subject_id": int(subject_id), "permissions": clean},
        )
        return {
            "id": str(persisted_id),
            "item_id": str(item_id),
            "subject_type": "user",
            "subject_id": str(subject_id),
            "permissions": persisted_permissions,
            "expires_at": persisted_expiry,
        }

    def create_download_ticket(
        self,
        user_id: int,
        item_id: int,
        version_id: Optional[int] = None,
        ttl_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Create a short-lived opaque ticket for an authorized media object."""
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "download")
        media_object_id = item.get("media_object_id")
        if not media_object_id:
            raise StashValidationError("Stash item has no downloadable media object")

        selected_version = None
        if version_id is not None:
            selected_version = self._db.fetch_one(
                "SELECT * FROM stash_versions WHERE id = ? AND item_id = ?",
                (int(version_id), int(item_id)),
            )
            if not selected_version:
                raise StashNotFoundError("Stash version not found")
            if selected_version.get("media_object_id"):
                media_object_id = selected_version["media_object_id"]
        try:
            ttl = max(1, min(int(ttl_seconds), 300))
        except (TypeError, ValueError) as exc:
            raise StashValidationError("ttl_seconds must be an integer") from exc

        now = int(time.time() * 1000)
        expires_at = now + ttl * 1000
        ticket = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO stash_download_tickets "
            "(id, item_id, version_id, subject_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self._generate_id(),
                int(item_id),
                int(version_id) if version_id is not None else None,
                int(user_id),
                hashlib.sha256(ticket.encode("utf-8")).hexdigest(),
                expires_at,
                now,
            ),
        )
        self._audit(
            "download_ticket_created",
            "item",
            int(item_id),
            user_id,
            {"version_id": version_id, "expires_at": expires_at},
        )
        return {
            "ticket": ticket,
            "item_id": str(item_id),
            "version_id": str(version_id) if version_id is not None else None,
            "media_object_id": str(media_object_id),
            "expires_at": expires_at,
        }

    def _ticket_target(
        self, user_id: int, ticket: str
    ) -> Tuple[Dict[str, Any], Optional[Any]]:
        """Resolve a ticket's row + target media object id without consuming it.

        Validates the token, its subject/expiry, the item's liveness, and the
        caller's ``download`` authorization. Returns ``(row, media_object_id)``
        so the caller can sign a URL *before* the ticket is marked redeemed.
        """
        if not ticket or len(ticket) > 256:
            raise StashValidationError("Invalid download ticket")
        token_hash = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
        now = int(time.time() * 1000)
        row = self._db.fetch_one(
            "SELECT * FROM stash_download_tickets WHERE token_hash = ? "
            "AND subject_id = ? AND redeemed_at IS NULL AND expires_at > ?",
            (token_hash, int(user_id), now),
        )
        if not row:
            raise StashPermissionError(
                "Download ticket is invalid, expired, or already redeemed"
            )
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0",
            (int(row["item_id"]),),
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "download")
        version_id = row.get("version_id")
        media_object_id = item.get("media_object_id")
        if version_id is not None:
            version = self._db.fetch_one(
                "SELECT media_object_id FROM stash_versions WHERE id = ? AND item_id = ?",
                (int(version_id), int(row["item_id"])),
            )
            if version and version.get("media_object_id"):
                media_object_id = version["media_object_id"]
        return row, media_object_id

    def resolve_download_ticket(self, user_id: int, ticket: str) -> Dict[str, Any]:
        """Resolve a ticket's media target *without* consuming it.

        Lets the route sign a media URL first, so a transient media-storage
        failure cannot burn a one-time ticket with nothing returned.
        """
        row, media_object_id = self._ticket_target(user_id, ticket)
        version_id = row.get("version_id")
        return {
            "item_id": str(row["item_id"]),
            "version_id": str(version_id) if version_id is not None else None,
            "media_object_id": str(media_object_id) if media_object_id else None,
            "expires_at": int(row["expires_at"]),
        }

    def redeem_download_ticket(self, user_id: int, ticket: str) -> Dict[str, Any]:
        """Redeem a ticket once, rechecking item authorization at redemption."""
        row, media_object_id = self._ticket_target(user_id, ticket)
        version_id = row.get("version_id")
        now = int(time.time() * 1000)
        cursor = self._db.execute(
            "UPDATE stash_download_tickets SET redeemed_at = ? "
            "WHERE id = ? AND redeemed_at IS NULL AND expires_at > ?",
            (now, int(row["id"]), now),
        )
        try:
            if int(getattr(cursor, "rowcount", 0)) != 1:
                raise StashPermissionError(
                    "Download ticket is invalid, expired, or already redeemed"
                )
        finally:
            cursor.close()
        self._audit(
            "download_ticket_redeemed",
            "item",
            int(row["item_id"]),
            user_id,
            {"version_id": version_id},
        )
        return {
            "item_id": str(row["item_id"]),
            "version_id": str(version_id) if version_id is not None else None,
            "media_object_id": str(media_object_id) if media_object_id else None,
            "expires_at": int(row["expires_at"]),
        }

    def revoke_grant(self, user_id: int, grant_id: int) -> None:
        """Revoke a grant after checking that the caller owns the item."""
        grant = self._db.fetch_one(
            "SELECT g.*, i.owner_id FROM stash_grants g "
            "JOIN stash_items i ON i.id = g.item_id WHERE g.id = ? AND i.deleted = 0",
            (int(grant_id),),
        )
        if not grant:
            raise StashNotFoundError("Stash grant not found")
        item = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ?", (int(grant["item_id"]),)
        )
        if not item:
            raise StashNotFoundError("Stash item not found")
        self._authorize(item, user_id, "share", mutation=True)
        self._db.execute(
            "UPDATE stash_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (int(time.time() * 1000), int(grant_id)),
        )
        self._audit(
            "revoke_share",
            "grant",
            int(grant_id),
            user_id,
            {"item_id": int(grant["item_id"])},
        )

    def cleanup_expired_tickets(self) -> int:
        """Remove redeemed and expired ticket rows without touching audit history."""
        now = int(time.time() * 1000)
        cursor = self._db.execute(
            "DELETE FROM stash_download_tickets WHERE expires_at <= ? OR redeemed_at IS NOT NULL",
            (now,),
        )
        try:
            return max(0, int(getattr(cursor, "rowcount", 0)))
        finally:
            cursor.close()

    def purge_deleted_items(self, retention_days: int = 30) -> int:
        """Hard-delete stash items soft-deleted longer than ``retention_days`` ago.

        Child rows (versions, grants, tickets) are removed first for
        databases with foreign keys enabled. Referenced media is permanently
        deleted only when it is still owned by the item owner and no other
        active Stash item references it. Compliance audit rows are retained.
        Items within the retention window remain restorable by their owner or
        server manager.
        """
        try:
            retention_days = max(1, int(retention_days))
        except (TypeError, ValueError):
            retention_days = 30
        cutoff = int(time.time() * 1000) - retention_days * 24 * 60 * 60 * 1000
        ids = [
            row["id"]
            for row in self._db.fetch_all(
                "SELECT id FROM stash_items WHERE deleted = 1 AND updated_at <= ?",
                (cutoff,),
            )
        ]
        purged_count = 0
        for item_id in ids:
            item = self._db.fetch_one(
                "SELECT owner_id, media_object_id FROM stash_items WHERE id = ?",
                (item_id,),
            )
            if item:
                media_refs = {
                    str(item["media_object_id"])
                    for row in [item]
                    if row.get("media_object_id")
                }
                version_rows = self._db.fetch_all(
                    "SELECT media_object_id FROM stash_versions WHERE item_id = ?",
                    (item_id,),
                )
                media_refs.update(
                    str(row["media_object_id"])
                    for row in version_rows
                    if row.get("media_object_id")
                )
                cleanup_failed = bool(media_refs) and self._media is None
                for media_object_id in media_refs:
                    if self._media is None:
                        break
                    refs = self._db.fetch_one(
                        "SELECT COUNT(*) AS count FROM stash_items "
                        "WHERE deleted = 0 AND media_object_id = ? AND id <> ?",
                        (media_object_id, item_id),
                    )
                    version_refs = self._db.fetch_one(
                        "SELECT COUNT(*) AS count FROM stash_versions v "
                        "JOIN stash_items i ON i.id = v.item_id "
                        "WHERE i.deleted = 0 AND v.media_object_id = ? AND v.item_id <> ?",
                        (media_object_id, item_id),
                    )
                    if (refs and int(refs["count"]) > 0) or (
                        version_refs and int(version_refs["count"]) > 0
                    ):
                        continue
                    try:
                        media_file: Optional[Any] = None
                        # Normal resolution intentionally hides soft-deleted
                        # media. Retention cleanup uses the media boundary's
                        # internal purge lookup so a previously soft-deleted
                        # file does not remain orphaned forever.
                        if str(media_object_id).isdigit():
                            purge_lookup = getattr(
                                self._media, "get_file_for_permanent_delete", None
                            )
                            if callable(purge_lookup):
                                media_file = purge_lookup(int(media_object_id))
                        if media_file is None:
                            purge_lookup_by_name = getattr(
                                self._media,
                                "get_file_by_filename_for_permanent_delete",
                                None,
                            )
                            if callable(purge_lookup_by_name):
                                media_file = purge_lookup_by_name(str(media_object_id))
                        if media_file is None:
                            media_file = self._resolve_media_object(
                                media_object_id, int(item["owner_id"])
                            )
                        if media_file is None:
                            raise StashStorageError(
                                "Referenced media object could not be resolved for purge"
                            )
                        if media_file is not None:
                            can_delete = getattr(
                                self._media, "can_delete_file_permanently", None
                            )
                            if not callable(can_delete) or not can_delete(
                                int(media_file.id)
                            ):
                                raise StashStorageError(
                                    "Media object is still referenced outside PlexiStash"
                                )
                            self._media.delete_file_permanently(
                                int(item["owner_id"]), int(media_file.id)
                            )
                    except Exception as exc:
                        import utils.logger as logger

                        logger.warning(
                            "Stash media cleanup deferred for item %s: %s",
                            item_id,
                            exc,
                        )
                        cleanup_failed = True
                if cleanup_failed:
                    # Keep metadata for a later retention pass; deleting it
                    # now would make an undeletable object untraceable.
                    continue
            for query in (
                "DELETE FROM stash_download_tickets WHERE item_id = ?",
                "DELETE FROM stash_grants WHERE item_id = ?",
                "DELETE FROM stash_versions WHERE item_id = ?",
            ):
                try:
                    self._db.execute(query, (item_id,))
                except Exception as exc:
                    import utils.logger as logger

                    logger.debug("Stash purge child cleanup skipped: %s", exc)
            # Deliberately retain suite_audit_log for compliance and forensic
            # history; content retention must not erase the security trail.
            self._db.execute("DELETE FROM stash_items WHERE id = ?", (item_id,))
            purged_count += 1
        return purged_count

    def create_collection(
        self, user_id: int, name: str, server_id: Optional[int] = None
    ) -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name or len(name) > 200:
            raise StashValidationError(
                "collection name must be between 1 and 200 characters"
            )
        if server_id is not None and not self._is_member(user_id, int(server_id)):
            raise StashPermissionError("You are not a member of this server")
        collection_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.execute(
            "INSERT INTO stash_collections (id, owner_id, name, server_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (collection_id, user_id, name, server_id, now, now),
        )
        self._audit(
            "create",
            "collection",
            collection_id,
            user_id,
            {"scope": "server" if server_id is not None else "personal"},
        )
        return {
            "id": str(collection_id),
            "owner_id": str(user_id),
            "name": name,
            "server_id": str(server_id) if server_id is not None else None,
            "created_at": now,
            "updated_at": now,
        }

    def list_collections(
        self, user_id: int, server_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        clauses = [
            "(owner_id = ? OR (server_id IS NOT NULL AND server_id IN (SELECT server_id FROM srv_members WHERE user_id = ?)))"
        ]
        params: List[Any] = [user_id, user_id]
        if server_id is not None:
            if not self._is_member(user_id, int(server_id)):
                return []
            clauses.append("server_id = ?")
            params.append(int(server_id))
        rows = self._db.fetch_all(
            f"SELECT * FROM stash_collections WHERE {' AND '.join(clauses)} ORDER BY name",
            tuple(params),
        )
        return [
            {
                "id": str(row["id"]),
                "owner_id": str(row["owner_id"]),
                "name": row["name"],
                "server_id": str(row["server_id"])
                if row.get("server_id") is not None
                else None,
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            }
            for row in rows
        ]
