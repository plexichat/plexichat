"""Stash manager mixin: item CRUD, versions, grants, download tickets (split from manager.py)."""

import hashlib
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from .schema import (
    PERSONAL_ITEM_GRANTABLE_PERMISSIONS,
    STASH_ITEM_TYPES,
    STASH_PERMISSIONS,
)
from .stash_manager_protocol import StashManagerProtocol
from ._shared import (
    StashNotFoundError,
    StashPermissionError,
    StashStorageError,
    StashValidationError,
)


class StashManagerItems(StashManagerProtocol):
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
        if media_object_id is not None:
            self._index_item_text(item_id, media_object_id)
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
        cursor: Optional[str] = None,
        search: Optional[str] = None,
        scope: Optional[str] = None,
        server_id: Optional[int] = None,
        pinned: Optional[bool] = None,
        collection_id: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        limit = max(1, min(int(limit), 100))
        offset = self._resolve_list_offset(offset, cursor)
        return self._query_item_page(
            user_id,
            limit=limit,
            offset=offset,
            search=search,
            scope=scope,
            server_id=server_id,
            pinned=pinned,
            collection_id=collection_id,
        )

    def list_items_page(
        self,
        user_id: int,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
        search: Optional[str] = None,
        scope: Optional[str] = None,
        server_id: Optional[int] = None,
        pinned: Optional[bool] = None,
        collection_id: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int, Optional[str]]:
        """Cursor-paginated item listing.

        ``cursor`` is the opaque ``next_cursor`` value from a previous page
        (a decimal row offset). ``limit`` is clamped to ``[1, 100]``. Returns
        ``(items, total, next_cursor)`` where ``next_cursor`` is ``None`` when
        the page reaches the end, so callers can enumerate every visible item
        with no silent truncation.
        """
        limit = max(1, min(int(limit), 100))
        offset = self._resolve_list_offset(0, cursor)
        items, total = self._query_item_page(
            user_id,
            limit=limit,
            offset=offset,
            search=search,
            scope=scope,
            server_id=server_id,
            pinned=pinned,
            collection_id=collection_id,
        )
        next_cursor: Optional[str] = None
        if offset + len(items) < total:
            next_cursor = str(offset + len(items))
        return items, total, next_cursor

    @staticmethod
    def _resolve_list_offset(offset: int, cursor: Optional[str]) -> int:
        """Resolve the effective row offset from ``offset``/``cursor``.

        An explicit ``cursor`` (opaque offset issued as ``next_cursor``)
        overrides ``offset``. Garbage cursors fail closed with a validation
        error instead of silently restarting or skipping rows.
        """
        if cursor is None:
            return max(0, int(offset))
        try:
            resolved = int(str(cursor).strip())
        except (TypeError, ValueError) as exc:
            raise StashValidationError("Invalid pagination cursor") from exc
        if resolved < 0:
            raise StashValidationError("Invalid pagination cursor")
        return resolved

    def _query_item_page(
        self,
        user_id: int,
        *,
        limit: int,
        offset: int,
        search: Optional[str] = None,
        scope: Optional[str] = None,
        server_id: Optional[int] = None,
        pinned: Optional[bool] = None,
        collection_id: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        now = int(time.time() * 1000)
        # Keyset-paginate the user's grants (cursor over item_id) so a user
        # with more than one batch of grants still sees every granted item.
        # The previous silent ``[:500]`` truncation dropped the remainder,
        # making granted items vanish from listings purely because of where
        # their grant row happened to sort.
        grant_item_ids = []
        grant_cursor = -1
        while True:
            grant_rows = self._db.fetch_all(
                "SELECT item_id, permissions_json FROM stash_grants "
                "WHERE subject_type = 'user' AND subject_id = ? AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?) AND item_id > ? "
                "ORDER BY item_id LIMIT 500",
                (str(user_id), now, grant_cursor),
            )
            if not grant_rows:
                break
            for grant_row in grant_rows:
                permissions = self._decode(grant_row.get("permissions_json"), [])
                if "view_metadata" in permissions or "*" in permissions:
                    grant_item_ids.append(int(grant_row["item_id"]))
            grant_cursor = int(grant_rows[-1]["item_id"])
        grant_item_ids.extend(self._collection_granted_item_ids(user_id))
        # De-duplicate preserving order; no truncation — the IN-clause is
        # emitted in bounded chunks below so the full visibility set survives.
        grant_item_ids = list(dict.fromkeys(grant_item_ids))

        clauses = ["deleted = 0"]
        if grant_item_ids:
            id_clauses: List[str] = []
            params: List[Any] = [user_id, user_id]
            for chunk_start in range(0, len(grant_item_ids), 500):
                chunk = grant_item_ids[chunk_start : chunk_start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                id_clauses.append(f"id IN ({placeholders})")
                params.extend(chunk)
            clauses.append(
                "(owner_id = ? OR "
                "(scope = 'server' AND server_id IN "
                "(SELECT server_id FROM srv_members WHERE user_id = ?)) OR "
                + " OR ".join(id_clauses)
                + ")"
            )
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
        if collection_id is not None:
            # Items may also be surfaced via a collection grant; the visibility
            # clauses above still gate access, this only narrows to a folder.
            clauses.append("collection_id = ?")
            params.append(int(collection_id))
        if search:
            # Escape LIKE metacharacters so ``%``/``_``/``\`` are matched
            # literally instead of acting as wildcards
            # (docs/admin-hardening.md).
            escaped = (
                str(search)
                .strip()
                .lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            needle = f"%{escaped}%"
            clauses.append(
                "(LOWER(title) LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(description, '')) LIKE ? ESCAPE '\\' "
                "OR LOWER(tags_json) LIKE ? ESCAPE '\\' "
                "OR EXISTS (SELECT 1 FROM stash_item_text t "
                "WHERE t.item_id = stash_items.id "
                "AND LOWER(t.content_text) LIKE ? ESCAPE '\\'))"
            )
            params.extend([needle, needle, needle, needle])
        where = " AND ".join(clauses)
        count_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS count FROM stash_items WHERE {where}", tuple(params)
        )
        rows = self._db.fetch_all(
            f"SELECT * FROM stash_items WHERE {where} ORDER BY pinned DESC, updated_at DESC LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        )
        total = int(count_row["count"]) if count_row else 0
        exposure = self._can_expose_storage_batch(rows, user_id)
        return [
            self._row_to_item(row, include_storage=exposure[int(row["id"])])
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
        if media_object_id is not None:
            self._index_item_text(int(item_id), media_object_id)
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
        share_link_id = row.get("share_link_id")
        if share_link_id is not None:
            # Share-link-issued ticket: the link itself is the authorization.
            # Re-check it at redemption so a revoked/expired link invalidates
            # outstanding tickets even though the recipient holds a valid
            # ticket token.
            link = self._db.fetch_one(
                "SELECT revoked_at, expires_at FROM stash_share_links WHERE id = ?",
                (int(share_link_id),),
            )
            if not link or link.get("revoked_at") is not None:
                raise StashPermissionError("Share link was revoked")
            if link.get("expires_at") is not None and int(link["expires_at"]) <= now:
                raise StashPermissionError("Share link has expired")
        else:
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
