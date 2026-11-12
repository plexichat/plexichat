import time
from typing import Any, Optional

from .._shared import (
    StashStorageError,
)
from ..stash_manager_protocol import StashManagerProtocol


class ItemMaintenanceMixin(StashManagerProtocol):
    """Ticket cleanup and deleted-item purging."""

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
