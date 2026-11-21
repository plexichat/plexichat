import time
from typing import Any, Dict

from ...boundaries import (
    MAX_WORKSPACE_DESCRIPTION_LENGTH,
    MAX_WORKSPACE_TITLE_LENGTH,
    bound_workspace_text,
)
from .._shared import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)
from ..stash_manager_protocol import StashManagerProtocol


class ItemMutationMixin(StashManagerProtocol):
    """Item updates, pinning, deletion, and restore."""

    def update_item(self, user_id: int, item_id: int, **fields: Any) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM stash_items WHERE id = ? AND deleted = 0", (int(item_id),)
        )
        if not row:
            raise StashNotFoundError("Stash item not found")
        self._authorize(row, user_id, "edit", mutation=True)
        updates: Dict[str, Any] = {}
        if "title" in fields:
            # Creation routes title through ``bound_workspace_text``; apply the
            # same boundary here so an editor cannot bypass the length limit or
            # smuggle control characters (log/response forging) via an update.
            updates["title"] = bound_workspace_text(
                fields["title"],
                "title",
                max_length=MAX_WORKSPACE_TITLE_LENGTH,
                required=True,
                error_cls=StashValidationError,
            )
        if "description" in fields:
            # Creation bounds description to MAX_WORKSPACE_DESCRIPTION_LENGTH
            # and rejects control characters; the update path previously wrote
            # the raw value, allowing an arbitrarily large blob (re-decoded on
            # every read/listing) and control characters into stored data.
            updates["description"] = bound_workspace_text(
                fields["description"],
                "description",
                max_length=MAX_WORKSPACE_DESCRIPTION_LENGTH,
                error_cls=StashValidationError,
            )
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
            bounded_metadata = self._bound_metadata(fields["metadata"])
            self._validate_link_metadata(row["item_type"], bounded_metadata)
            updates["metadata_json"] = self._json(bounded_metadata, {})
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
            except Exception as permission_error:
                import utils.logger as logger

                logger.debug(f"stash permission check failed: {permission_error}")
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
        # SECURITY: only a genuine integer id from the authoritative item row
        # may attest ownership. int() laundering of a malformed value
        # ("05"->5, 5.9->5, True->1) would let a non-owner restore another
        # user's item -- the restore UPDATE has no SQL owner backstop. type()
        # also rejects bool.
        row_owner_id = row["owner_id"]
        owner = type(row_owner_id) is int and row_owner_id == int(user_id)
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
