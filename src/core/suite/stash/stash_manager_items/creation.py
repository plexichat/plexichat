import time
from typing import Any, Dict, List, Optional, cast

from ...boundaries import (
    MAX_WORKSPACE_DESCRIPTION_LENGTH,
    MAX_WORKSPACE_TITLE_LENGTH,
    bound_workspace_tags,
    bound_workspace_text,
)
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
        title = cast(
            str,
            bound_workspace_text(
                title,
                "title",
                max_length=MAX_WORKSPACE_TITLE_LENGTH,
                required=True,
                error_cls=StashValidationError,
            ),
        )
        description = bound_workspace_text(
            description,
            "description",
            max_length=MAX_WORKSPACE_DESCRIPTION_LENGTH,
            error_cls=StashValidationError,
        )
        scope, server_id = self._validate_scope(scope, server_id)
        if (
            scope == "server"
            and server_id is not None
            and not self._is_member(user_id, int(server_id))
        ):
            raise StashPermissionError("You are not a member of this server")
        self._validate_collection(user_id, collection_id, scope, server_id)
        metadata = self._bound_metadata(metadata)
        self._validate_link_metadata(item_type, metadata)
        self._resolve_media_object(media_object_id, user_id)
        now = int(time.time() * 1000)
        item_id = self._generate_id()
        clean_tags = bound_workspace_tags(tags)
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
        # SECURITY: only a genuine integer id from the authoritative item row
        # may attest ownership. int() laundering of a malformed value
        # ("05"->5, 5.9->5, True->1) would disclose the owner-only grant list.
        # type() also rejects bool.
        row_owner_id = row["owner_id"]
        is_owner = type(row_owner_id) is int and row_owner_id == int(user_id)
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
        row_owner_id = row["owner_id"]
        is_owner = type(row_owner_id) is int and row_owner_id == int(user_id)
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
