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

from src.core.base import BaseManager
from .schema import STASH_ITEM_TYPES, STASH_PERMISSIONS


class StashNotFoundError(LookupError):
    """Raised when an item is absent or not visible to the caller."""


class StashPermissionError(PermissionError):
    """Raised when a caller lacks the requested Stash permission."""


class StashValidationError(ValueError):
    """Raised for malformed Stash metadata."""


class StashManager(BaseManager):
    """Persistence and authorization boundary for PlexiStash metadata."""

    def __init__(self, db: Any, servers_module: Optional[Any] = None) -> None:
        super().__init__(db)
        self._servers = servers_module

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
        return permission in permissions or "*" in permissions

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
                # item grant.
                if owner or self._can_manage_server(user_id, int(server_id)):
                    return
            elif permission in ("preview", "download"):
                # Storage exposure is grant-gated even within a server:
                # seeing metadata never implies permission to fetch bytes.
                if (
                    owner
                    or self._can_manage_server(user_id, int(server_id))
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
        if server_id is not None and self._can_manage_server(user_id, int(server_id)):
            return True
        return self._grant_permissions(
            int(row["id"]), user_id, "preview"
        ) or self._grant_permissions(int(row["id"]), user_id, "download")

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
        return self._row_to_item(
            row, include_storage=self._can_expose_storage(row, user_id)
        )

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
            updates["media_object_id"] = fields["media_object_id"]
        if "metadata" in fields:
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
                "move" if set(updates) == {"collection_id"} else "edit",
                "item",
                int(item_id),
                user_id,
                {"fields": sorted(updates)},
            )
        return self.get_item(user_id, item_id)

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
        return self.get_item(user_id, item_id)

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
            and self._can_manage_server(user_id, int(server_id))
        )
        if not owner and not server_manager:
            raise StashPermissionError(
                "Only the owner or server manager can restore this item"
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
        version_id = self._generate_id()
        now = int(time.time() * 1000)
        self._db.execute(
            "INSERT INTO stash_versions (id, item_id, created_by, media_object_id, content_type, size_bytes, checksum, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                item_id,
                user_id,
                fields.get("media_object_id"),
                fields.get("content_type"),
                fields.get("size_bytes"),
                fields.get("checksum"),
                now,
            ),
        )
        self._db.execute(
            "UPDATE stash_items SET current_version_id = ?, media_object_id = COALESCE(?, media_object_id), updated_at = ? WHERE id = ?",
            (version_id, fields.get("media_object_id"), now, item_id),
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
            ["permissions_json", "expires_at", "revoked_at", "created_by"],
        )
        self._audit(
            "share",
            "item",
            int(item_id),
            user_id,
            {"subject_id": int(subject_id), "permissions": clean},
        )
        return {
            "id": str(grant_id),
            "item_id": str(item_id),
            "subject_type": "user",
            "subject_id": str(subject_id),
            "permissions": clean,
            "expires_at": expires_at,
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

    def redeem_download_ticket(self, user_id: int, ticket: str) -> Dict[str, Any]:
        """Redeem a ticket once, rechecking item authorization at redemption."""
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

        Child rows (versions, grants, tickets, audit) are removed first for
        databases with foreign keys enabled. Items within the retention window
        remain restorable by their owner or server manager.
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
        for item_id in ids:
            for query in (
                "DELETE FROM stash_download_tickets WHERE item_id = ?",
                "DELETE FROM stash_grants WHERE item_id = ?",
                "DELETE FROM stash_versions WHERE item_id = ?",
                "DELETE FROM suite_audit_log WHERE resource_type = 'item' AND resource_id = ?",
            ):
                try:
                    self._db.execute(query, (item_id,))
                except Exception as exc:
                    import utils.logger as logger

                    logger.debug("Stash purge child cleanup skipped: %s", exc)
            self._db.execute("DELETE FROM stash_items WHERE id = ?", (item_id,))
        return len(ids)

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
