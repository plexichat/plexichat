import time
from typing import Any, Dict, List, Optional, Tuple

from .._shared import (
    StashValidationError,
)
from ..stash_manager_protocol import StashManagerProtocol


class ItemListingMixin(StashManagerProtocol):
    """Paginated item listing and the search query builder."""

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
