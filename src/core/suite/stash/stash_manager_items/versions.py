import time
from typing import Any, Dict, List

from .._shared import (
    StashNotFoundError,
)
from ..stash_manager_protocol import StashManagerProtocol


class ItemVersionMixin(StashManagerProtocol):
    """Item version creation and reads."""

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
