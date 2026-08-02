"""PlexiSuite metadata collector for DSAR exports.

Only metadata owned by, granted to, or created by the requesting user is
returned. Binary content remains in the media subsystem and is not copied into
an export by this collector.
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
            "sum_sheets": self._optional_rows(
                """SELECT s.* FROM sum_sheets s
                   JOIN sum_workbooks w ON w.id = s.workbook_id
                   WHERE w.owner_id = ?""",
                (user_id,),
            ),
            "sum_cells": self._optional_rows(
                """SELECT c.* FROM sum_cells c
                   JOIN sum_workbooks w ON w.id = c.workbook_id
                   WHERE w.owner_id = ?""",
                (user_id,),
            ),
            "sum_operations": self._optional_rows(
                """SELECT * FROM sum_operations
                   WHERE actor_id = ? OR workbook_id IN
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
                      OR resource_id IN (SELECT id FROM sum_workbooks WHERE owner_id = ?)
                      OR resource_id IN (SELECT id FROM sum_operations WHERE actor_id = ?)""",
                (user_id, user_id, user_id, user_id, user_id, user_id, user_id),
            ),
        }

    def _optional_rows(self, query: str, params: tuple) -> List[Dict[str, Any]]:
        """Return rows, treating pre-Suite installations as empty."""
        try:
            return [dict(row) for row in self._db.fetch_all(query, params)]
        except Exception:
            return []
