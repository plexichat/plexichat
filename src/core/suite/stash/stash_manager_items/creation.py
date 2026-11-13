import time
from typing import Any, Dict, List, Optional

from .._shared import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)
from ..schema import (
    STASH_ITEM_TYPES,
)
from ..stash_manager_protocol import StashManagerProtocol


class ItemCreationMixin(StashManagerProtocol):
    """Create and read Stash items."""

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
