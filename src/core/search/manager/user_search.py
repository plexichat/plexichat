from typing import List, Optional, Set

from .base import SearchManagerBase
from .message_search import validate_search_query
from ..exceptions import SearchLimitError
from ..models import IndexedUser, UserSearchResult, UserSearchResultPage


class UserSearchMixin(SearchManagerBase):
    # Bound the directory pool fetched when a server scope is enforced. The
    # membership set is filtered in Python rather than splatted into an
    # unbounded ``IN (...)`` (a large server can have thousands of members),
    # so fetch a little more than needed and stop once the slot is filled.
    _SCOPED_SUPPLEMENTAL_POOL_FACTOR = 8
    _SCOPED_SUPPLEMENTAL_POOL_CAP = 500

    def _supplemental_user_results(
        self,
        query: str,
        exclude_ids: Set[int],
        limit: int,
        offset: int = 0,
        allowed_user_ids: Optional[Set[int]] = None,
    ) -> List[UserSearchResult]:
        """Directory fallback for matches the FTS user index missed.

        The FTS index only contains users that were explicitly indexed and can
        lag registrations or username changes. This fallback:
          - matches *partial* usernames case-insensitively against auth_users
          - resolves exact email addresses through the blind index (emails are
            encrypted at rest, so only exact-match lookup is possible)

        ``offset`` pages into the directory results so this fallback never
        re-adds users a previous page already skipped (offset pagination
        correctness).

        ``allowed_user_ids`` carries a caller's scope (e.g. the member set of a
        server-scoped search). When present, a directory match is only returned
        if its id is in the set, so the fallback cannot widen a scoped search
        back into the whole directory (scope/IDOR bypass).
        """
        results: List[UserSearchResult] = []
        q = (query or "").strip()
        if not q or limit <= 0:
            return results

        seen = set(exclude_ids)

        def _allowed(uid: int) -> bool:
            return allowed_user_ids is None or uid in allowed_user_ids

        if "@" in q and offset <= 0:
            email_index = None
            try:
                from src.utils.encryption import blind_index

                email_index = blind_index(q, "user_email")
            except Exception as e:
                import utils.logger as logger

                logger.debug(f"User search email index failed: {e}")
            if email_index:
                row = self._db.fetch_one(
                    "SELECT id, username FROM auth_users "
                    "WHERE email_index = ? AND deletion_status = 'active'",
                    (email_index,),
                )
                if row:
                    uid = int(row["id"])
                    if uid not in seen and _allowed(uid):
                        seen.add(uid)
                        results.append(
                            UserSearchResult(
                                id=uid,
                                user_id=uid,
                                username=row["username"] or "",
                            )
                        )

        remaining = limit - len(results)
        if remaining <= 0:
            return results

        escaped = (
            q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        # When a scope is enforced, a plain LIMIT of ``remaining`` would usually
        # be filled by out-of-scope users and return nothing. Over-fetch a
        # bounded pool and filter it in Python instead.
        pool = remaining
        if allowed_user_ids is not None:
            pool = min(
                max(remaining * self._SCOPED_SUPPLEMENTAL_POOL_FACTOR, 50),
                self._SCOPED_SUPPLEMENTAL_POOL_CAP,
            )
        rows = self._db.fetch_all(
            "SELECT id, username FROM auth_users "
            "WHERE deletion_status = 'active' AND LOWER(username) LIKE ? ESCAPE '\\' "
            "ORDER BY username LIMIT ? OFFSET ?",
            (f"%{escaped}%", pool, max(offset - len(results), 0)),
        )
        for row in rows or []:
            uid = int(row["id"])
            if uid in seen or not _allowed(uid):
                continue
            seen.add(uid)
            results.append(
                UserSearchResult(id=uid, user_id=uid, username=row["username"] or "")
            )
            if len(results) >= limit:
                break
        return results[:limit]

    def _merge_supplemental_users(
        self,
        query: str,
        results: List[UserSearchResult],
        limit: int,
        offset: int = 0,
        allowed_user_ids: Optional[Set[int]] = None,
    ) -> List[UserSearchResult]:
        """Append directory matches for users the indexer missed.

        ``offset`` is forwarded so the directory fallback skips the entries a
        previous page already consumed; without it, offset pagination would
        resurrect skipped users on every page. ``allowed_user_ids`` is the
        optional caller scope that the fallback must not escape.
        """
        if len(results) >= limit:
            return results
        supplemental = self._supplemental_user_results(
            query,
            {r.user_id for r in results},
            limit - len(results),
            offset=offset,
            allowed_user_ids=allowed_user_ids,
        )
        return list(results) + supplemental

    def search_users(
        self,
        user_id: int,
        query: str,
        server_id: Optional[int] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[UserSearchResult]:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        # F16: empty/whitespace queries must NOT fall through to the
        # supplemental directory fallback, whose ``LIKE '%q%'`` degenerates
        # to a whole-directory scan for an empty needle. Every endpoint now
        # answers an empty query with the same 400-class error.
        validate_search_query(query)

        self._check_rate_limit(user_id)

        results = self._indexer.search_users(query, limit * 2, offset)

        # Resolve the scope ONCE and apply it to every source of results. The
        # supplemental directory fallback used to run after this filter with
        # no scope of its own, so a server-scoped search could return users who
        # are not members of that server (scope/IDOR bypass).
        server_members: Optional[Set[int]] = None
        if server_id:
            server_members = self._get_server_member_ids(server_id)
            results = [r for r in results if r.user_id in server_members]

        results = self._merge_supplemental_users(
            query, results, limit, offset=offset, allowed_user_ids=server_members
        )
        results = self._enrich_user_results(results, user_id)

        results = self._ranking_engine.rank_user_results(results, query, user_id)

        return results[:limit]

    def search_users_page(
        self,
        user_id: int,
        query: str,
        server_id: Optional[int] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> UserSearchResultPage:
        max_limit = self._config.get("result_limit", 100)
        if limit > max_limit:
            raise SearchLimitError(
                f"Limit exceeds maximum of {max_limit}",
                max_allowed=max_limit,
                requested=limit,
            )

        # F16: same empty/oversized-query guard as the non-paged path so
        # both user-search endpoints agree with message search.
        validate_search_query(query)

        self._check_rate_limit(user_id)

        results, next_cursor = self._indexer.search_users_page(query, limit, cursor)

        # Mirror the offset path: the scope applies to the supplemental
        # directory fallback too, not just to the indexer hits.
        server_members: Optional[Set[int]] = None
        if server_id:
            server_members = self._get_server_member_ids(server_id)
            results = [r for r in results if r.user_id in server_members]

        results = self._merge_supplemental_users(
            query, results, limit, allowed_user_ids=server_members
        )
        results = self._enrich_user_results(results, user_id)
        results = self._ranking_engine.rank_user_results(results, query, user_id)

        return UserSearchResultPage(results=results, next_cursor=next_cursor)

    def index_user(
        self,
        user_id: int,
        username: str,
        display_name: Optional[str] = None,
        is_bot: bool = False,
    ) -> None:
        indexed = IndexedUser(
            user_id=user_id,
            username=username,
            display_name=display_name,
            is_bot=is_bot,
        )

        self._indexer.index_user(indexed)

        now = self._get_timestamp()
        self._db.upsert(
            "search_user_index",
            ["user_id", "indexed_at", "updated_at"],
            (user_id, now, now),
            ["user_id"],
            ["updated_at"],
        )
