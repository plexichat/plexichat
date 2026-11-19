import base64
import binascii
import time
from typing import Any, Dict, List, Optional, Tuple

from .._shared import (
    StashValidationError,
)
from ..stash_manager_protocol import StashManagerProtocol

_KEYSET_PREFIX = "k1."
_MAX_KEYSET_CURSOR_LENGTH = 128


class ItemKeysetCursor:
    """Opaque ``(pinned, updated_at, id)`` anchor for keyset item paging.

    The legacy cursor is a decimal row offset. Tokens produced here carry an
    explicit ``k1.`` prefix so the two schemes can coexist: a decimal value
    always keeps its historic offset meaning, while a ``k1.`` token always
    takes the seek path. The three-part anchor mirrors the listing sort order
    (``pinned DESC, updated_at DESC``) with ``id`` as a stable tiebreaker, so a
    deep page costs the same as page one instead of walking skipped rows.
    """

    @staticmethod
    def encode(pinned: Any, updated_at: Any, item_id: Any) -> str:
        raw = f"{int(pinned)}:{int(updated_at)}:{int(item_id)}".encode("ascii")
        return _KEYSET_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip(
            "="
        )

    @staticmethod
    def is_keyset_token(token: Optional[str]) -> bool:
        return isinstance(token, str) and token.startswith(_KEYSET_PREFIX)

    @staticmethod
    def decode(token: Optional[str]) -> Optional[Tuple[int, int, int]]:
        """Return the seek anchor, or ``None`` for no/legacy cursor.

        A malformed prefixed token fails closed with ``StashValidationError``
        instead of silently restarting the listing or skipping rows. Bounds on
        the token length and on each component keep a client from injecting a
        huge range predicate.
        """
        if not ItemKeysetCursor.is_keyset_token(token):
            return None
        assert token is not None
        if len(token) > _MAX_KEYSET_CURSOR_LENGTH:
            raise StashValidationError("Invalid pagination cursor")
        payload = token[len(_KEYSET_PREFIX) :]
        try:
            padded = payload + "=" * (-len(payload) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
            parts = raw.split(":")
            if len(parts) != 3 or not all(part.isdigit() for part in parts):
                raise ValueError("malformed keyset cursor")
            pinned, updated_at, item_id = (int(part) for part in parts)
            if (
                pinned not in (0, 1)
                or updated_at < 0
                or item_id <= 0
                or item_id >= 1 << 64
            ):
                raise ValueError("out-of-range keyset cursor")
            return pinned, updated_at, item_id
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise StashValidationError("Invalid pagination cursor") from exc


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
        if ItemKeysetCursor.is_keyset_token(cursor):
            items, total, _ = self._query_item_page_keyset(
                user_id,
                limit=limit,
                cursor=cursor,
                search=search,
                scope=scope,
                server_id=server_id,
                pinned=pinned,
                collection_id=collection_id,
            )
            return items, total
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
        keyset: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int, Optional[str]]:
        """Cursor-paginated item listing.

        ``cursor`` is the opaque ``next_cursor`` value from a previous page.
        Decimal cursors keep the legacy offset behaviour. ``keyset=True`` (or
        a ``k1.`` cursor issued by ``list_items_keyset``) switches to an index
        seek over ``(pinned, updated_at, id)`` so deep pages cost the same as
        page one. ``limit`` is clamped to ``[1, 100]``. Returns
        ``(items, total, next_cursor)`` where ``next_cursor`` is ``None`` when
        the page reaches the end, so callers can enumerate every visible item
        with no silent truncation.
        """
        limit = max(1, min(int(limit), 100))
        if keyset or ItemKeysetCursor.is_keyset_token(cursor):
            if (
                keyset
                and cursor is not None
                and not ItemKeysetCursor.is_keyset_token(cursor)
            ):
                raise StashValidationError("Invalid pagination cursor")
            return self._query_item_page_keyset(
                user_id,
                limit=limit,
                cursor=cursor,
                search=search,
                scope=scope,
                server_id=server_id,
                pinned=pinned,
                collection_id=collection_id,
            )
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

    def list_items_keyset(
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
        """Return one keyset (seek) page and the token for the following page.

        BEFORE: a page was addressed by a decimal OFFSET, so SQLite walked and
        discarded every skipped row and deep pages got progressively slower.

        AFTER: the page is addressed by the last visible row's
        ``(pinned, updated_at, id)`` anchor. A bounded ``pinned < ? OR ...``
        range predicate seeks through the existing owner/pinned indexes; cost is
        independent of page depth. One extra row is fetched purely to compute
        ``has_more``/``next_cursor`` and is never returned.

        The visibility, scope, server, pinned, collection and search clauses
        are shared verbatim with the offset path, so the two pages are identical
        and no authorization filter is relaxed.
        """
        limit = max(1, min(int(limit), 100))
        if cursor is not None and not ItemKeysetCursor.is_keyset_token(cursor):
            raise StashValidationError("Invalid pagination cursor")
        return self._query_item_page_keyset(
            user_id,
            limit=limit,
            cursor=cursor,
            search=search,
            scope=scope,
            server_id=server_id,
            pinned=pinned,
            collection_id=collection_id,
        )

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

    def _build_item_visibility_clause(
        self,
        user_id: int,
        *,
        search: Optional[str] = None,
        scope: Optional[str] = None,
        server_id: Optional[int] = None,
        pinned: Optional[bool] = None,
        collection_id: Optional[int] = None,
    ) -> Tuple[str, List[Any]]:
        """Build the shared visibility/filter WHERE clause and its params.

        Both the offset and keyset page queries call this, so a keyset page
        can never see a row the offset page would hide (or vice versa).
        """
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
                return "0 = 1", []
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
        return " AND ".join(clauses), params

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
        where, params = self._build_item_visibility_clause(
            user_id,
            search=search,
            scope=scope,
            server_id=server_id,
            pinned=pinned,
            collection_id=collection_id,
        )
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

    def _query_item_page_keyset(
        self,
        user_id: int,
        *,
        limit: int,
        cursor: Optional[str] = None,
        search: Optional[str] = None,
        scope: Optional[str] = None,
        server_id: Optional[int] = None,
        pinned: Optional[bool] = None,
        collection_id: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int, Optional[str]]:
        anchor = ItemKeysetCursor.decode(cursor)
        where, params = self._build_item_visibility_clause(
            user_id,
            search=search,
            scope=scope,
            server_id=server_id,
            pinned=pinned,
            collection_id=collection_id,
        )
        count_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS count FROM stash_items WHERE {where}", tuple(params)
        )
        total = int(count_row["count"]) if count_row else 0

        page_where = where
        page_params = list(params)
        if anchor is not None:
            anchor_pinned, anchor_updated, anchor_id = anchor
            # Expanded row comparison keeps this portable across SQLite and
            # Postgres: seek to rows strictly after the anchor in the same
            # DESC order, with id as the stable tiebreaker.
            page_where += (
                " AND (pinned < ? OR (pinned = ? AND "
                "(updated_at < ? OR (updated_at = ? AND id < ?))))"
            )
            page_params.extend(
                [
                    anchor_pinned,
                    anchor_pinned,
                    anchor_updated,
                    anchor_updated,
                    anchor_id,
                ]
            )
        # One-row lookahead computes has_more/next_cursor without returning it.
        page_params.append(limit + 1)
        rows = self._db.fetch_all(
            f"SELECT * FROM stash_items WHERE {page_where} "
            "ORDER BY pinned DESC, updated_at DESC, id DESC LIMIT ?",
            tuple(page_params),
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor: Optional[str] = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = ItemKeysetCursor.encode(
                last["pinned"], last["updated_at"], last["id"]
            )
        exposure = self._can_expose_storage_batch(page_rows, user_id)
        items = [
            self._row_to_item(row, include_storage=exposure[int(row["id"])])
            for row in page_rows
        ]
        return items, total, next_cursor
