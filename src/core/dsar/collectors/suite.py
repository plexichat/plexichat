"""PlexiSuite metadata collector for DSAR exports.

Only metadata owned by, granted to, or created by the requesting user is
returned. Workbook cell data lives inside ``.plexisum`` blob files on the
storage adapter (not the main database); blob content is not copied into an
export by this collector, matching the media collector's metadata-only
semantics. The main-database tables that exist are ``sum_workbooks``,
``sum_grants``, ``sum_range_permissions``, and ``sum_snapshots``.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class SuiteCollector(BaseCollector):
    """Collect Stash, Sum, and Suite audit metadata for one user."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        return {
            "stash_items": self._optional_rows(
                "SELECT * FROM stash_items WHERE owner_id = ?", (user_id,)
            ),
            "stash_collections": self._optional_rows(
                "SELECT * FROM stash_collections WHERE owner_id = ?", (user_id,)
            ),
            "stash_versions": self._optional_rows(
                """SELECT v.* FROM stash_versions v
                   JOIN stash_items i ON i.id = v.item_id
                   WHERE i.owner_id = ? OR v.created_by = ?""",
                (user_id, user_id),
            ),
            "stash_grants": self._optional_rows(
                """SELECT * FROM stash_grants
                   WHERE created_by = ? OR (subject_type = 'user' AND subject_id = ?)""",
                (user_id, str(user_id)),
            ),
            "stash_download_tickets": self._optional_rows(
                "SELECT * FROM stash_download_tickets WHERE subject_id = ?",
                (user_id,),
            ),
            "sum_workbooks": self._optional_rows(
                "SELECT * FROM sum_workbooks WHERE owner_id = ?", (user_id,)
            ),
            "sum_grants": self._optional_rows(
                """SELECT * FROM sum_grants WHERE created_by = ? OR subject_id = ?""",
                (user_id, user_id),
            ),
            "sum_range_permissions": self._optional_rows(
                """SELECT rp.* FROM sum_range_permissions rp
                   JOIN sum_workbooks w ON w.id = rp.workbook_id
                   WHERE w.owner_id = ?""",
                (user_id,),
            ),
            "sum_snapshots": self._optional_rows(
                """SELECT * FROM sum_snapshots
                   WHERE created_by = ? OR workbook_id IN
                     (SELECT id FROM sum_workbooks WHERE owner_id = ?)""",
                (user_id, user_id),
            ),
            "suite_audit_log": self._optional_rows(
                """SELECT * FROM suite_audit_log
                   WHERE actor_id = ?
                      OR resource_id IN (SELECT id FROM stash_items WHERE owner_id = ?)
                      OR resource_id IN (SELECT id FROM stash_collections WHERE owner_id = ?)
                      OR resource_id IN (SELECT id FROM stash_versions WHERE created_by = ?)
                      OR resource_id IN (SELECT id FROM stash_grants WHERE created_by = ?)
                      OR resource_id IN (SELECT id FROM sum_workbooks WHERE owner_id = ?)""",
                (user_id, user_id, user_id, user_id, user_id, user_id),
            ),
        }

    def _optional_rows(self, query: str, params: tuple) -> List[Dict[str, Any]]:
        """Return rows, treating pre-Suite installations as empty."""
        try:
            return [dict(row) for row in self._db.fetch_all(query, params)]
        except Exception as exc:
            import utils.logger as logger

            logger.error(
                f"Suite DSAR collector query failed ({query!r}, {params!r}): {exc}"
            )
            return []
