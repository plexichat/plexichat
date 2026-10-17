"""Stash manager mixin: construction, audit, authorization (split from manager.py)."""

import json
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .schema import (
    STASH_PERMISSIONS,
)

from .stash_manager_protocol import StashManagerProtocol
from ._shared import (
    StashPermissionError,
    StashStorageError,
    StashValidationError,
)


class StashManagerBase(StashManagerProtocol):
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

    def _collection_ancestors(self, collection_id: int) -> List[int]:
        """Return the collection id plus all of its ancestor collection ids.

        The traversal is cycle-guarded: a malformed parent chain cannot loop
        forever (or inflate into a pathological query), it simply terminates
        once an already-visited id is reached.
        """
        ids: List[int] = []
        seen = set()
        current: Optional[int] = int(collection_id)
        while current is not None and current not in seen:
            seen.add(current)
            ids.append(current)
            row = self._db.fetch_one(
                "SELECT parent_id FROM stash_collections WHERE id = ?",
                (current,),
            )
            current = (
                int(row["parent_id"])
                if row and row.get("parent_id") is not None
                else None
            )
        return ids

    def _collection_grant_permissions(
        self, item: Dict[str, Any], user_id: int
    ) -> List[str]:
        """Return the permissions inherited from the item's collection chain."""
        collection_id = item.get("collection_id")
        if collection_id is None:
            return []
        now = int(time.time() * 1000)
        permissions: set = set()
        for ancestor in self._collection_ancestors(int(collection_id)):
            row = self._db.fetch_one(
                "SELECT permissions_json, expires_at FROM stash_collection_grants "
                "WHERE collection_id = ? AND subject_id = ? AND revoked_at IS NULL",
                (ancestor, str(user_id)),
            )
            if not row:
                continue
            if row.get("expires_at") is not None and int(row["expires_at"]) <= now:
                continue
            permissions.update(self._decode(row.get("permissions_json"), []))
        return list(permissions)

    def _collection_granted(
        self, item: Dict[str, Any], user_id: int, permission: str
    ) -> bool:
        """Return whether ``permission`` is inherited from a collection grant."""
        permissions = self._collection_grant_permissions(item, user_id)
        if permission in permissions or "*" in permissions:
            return True
        if permission in ("view_metadata", "preview") and any(
            p in permissions
            for p in ("edit", "manage_versions", "move", "share", "delete")
        ):
            return True
        return False

    def _collection_descendants(self, collection_id: int) -> set:
        """Return a collection plus all of its descendant collection ids."""
        result = {int(collection_id)}
        frontier = [int(collection_id)]
        while frontier:
            next_frontier: List[int] = []
            for cid in frontier:
                rows = self._db.fetch_all(
                    "SELECT id FROM stash_collections WHERE parent_id = ?", (cid,)
                )
                for row in rows:
                    descendant = int(row["id"])
                    if descendant not in result:
                        result.add(descendant)
                        next_frontier.append(descendant)
            frontier = next_frontier
        return result

    def _collection_granted_item_ids(self, user_id: int) -> List[int]:
        """Return item ids visible through an inherited collection grant."""
        now = int(time.time() * 1000)
        grant_rows = self._db.fetch_all(
            "SELECT collection_id FROM stash_collection_grants "
            "WHERE subject_id = ? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (str(user_id), now),
        )
        if not grant_rows:
            return []
        visible_collections: set = set()
        for row in grant_rows:
            visible_collections.update(
                self._collection_descendants(int(row["collection_id"]))
            )
        if not visible_collections:
            return []
        placeholders = ", ".join("?" for _ in visible_collections)
        item_rows = self._db.fetch_all(
            f"SELECT id FROM stash_items WHERE deleted = 0 AND collection_id IN ({placeholders})",
            tuple(visible_collections),
        )
        return [int(row["id"]) for row in item_rows]

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
            if (
                owner
                or self._grant_permissions(int(item["id"]), user_id, permission)
                or self._collection_granted(item, user_id, permission)
            ):
                return
        elif server_id is not None and self._is_member(user_id, int(server_id)):
            if mutation:
                # Edits, shares, and deletes of server-scoped content are
                # governed by the server permission boundary. Server admins/
                # moderators may perform lifecycle mutations alongside
                # explicit server managers; explicit item grants (and grants
                # inherited through the item's collection chain) are honored
                # too so the granted permission vocabulary is actually
                # enforced instead of merely displayed.
                if (
                    owner
                    or self._can_administer_server(user_id, int(server_id))
                    or self._grant_permissions(int(item["id"]), user_id, permission)
                    or self._collection_granted(item, user_id, permission)
                ):
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

    def _can_expose_storage_batch(
        self, rows: List[Dict[str, Any]], user_id: int
    ) -> Dict[int, bool]:
        """Compute storage exposure for a whole page of rows in few queries.

        ``list_items`` renders up to ``limit`` rows; calling
        ``_can_expose_storage`` per row would fan out to three grant lookups
        plus a server-admin check *per item*. This batches the grant lookups
        into a single IN-clause query and memoizes the server-admin check per
        server, so a full page costs a handful of queries instead of hundreds.
        """
        if not rows:
            return {}
        item_ids = [int(row["id"]) for row in rows]
        placeholders = ", ".join("?" for _ in item_ids)
        now = int(time.time() * 1000)
        grant_rows = self._db.fetch_all(
            f"SELECT item_id, permissions_json, expires_at FROM stash_grants "
            f"WHERE subject_type = 'user' AND subject_id = ? "
            f"AND revoked_at IS NULL AND item_id IN ({placeholders})",
            (str(user_id), *item_ids),
        )
        exposed_by_grant: set = set()
        for grant_row in grant_rows:
            if (
                grant_row.get("expires_at") is not None
                and int(grant_row["expires_at"]) <= now
            ):
                continue
            permissions = self._decode(grant_row.get("permissions_json"), [])
            if "*" in permissions or any(
                p in permissions for p in ("edit", "preview", "download")
            ):
                exposed_by_grant.add(int(grant_row["item_id"]))

        admin_cache: Dict[int, bool] = {}
        result: Dict[int, bool] = {}
        for row in rows:
            item_id = int(row["id"])
            if int(row["owner_id"]) == int(user_id) or item_id in exposed_by_grant:
                result[item_id] = True
                continue
            server_id = row.get("server_id")
            if server_id is not None:
                sid = int(server_id)
                if sid not in admin_cache:
                    admin_cache[sid] = self._can_administer_server(user_id, sid)
                if admin_cache[sid]:
                    result[item_id] = True
                    continue
            result[item_id] = False
        return result

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
