from typing import List, Optional, Set

from .base import SearchManagerBase
from .message_search import validate_search_query
from .result_cache import CACHE_MISS, clear_result_caches, get_result_cache, make_key
from ..exceptions import SearchLimitError
from ..models import IndexedUser, UserSearchResult, UserSearchResultPage


class UserSearchMixin(SearchManagerBase):
    def clear_cache(self) -> None:
        # Message/server search memoize their final results for a short TTL;
        # user search was the one path that replayed the whole pipeline on
        # every identical request. Keep its memos in the same registry so an
        # explicit invalidation (permission/index change) drops all of them.
        super().clear_cache()
        clear_result_caches(self)

    @staticmethod
    def _scope_allows_viewer(user_id: int, server_members: Set[int]) -> bool:
        """Whether the caller belongs to a resolved server-visibility scope.

        A ``server_id`` scope is a visibility boundary, not merely a filter on
        hits: the caller must themselves be a member of that server. Otherwise
        ``/search/users?server_id=X`` is a roster-enumeration oracle for any
        authenticated caller who can guess a server id. Malformed scope data
        fails closed (returns False) rather than widening visibility.
        """
        try:
            # SECURITY: a srv_members.user_id vouches for the caller's scope
            # only while it is a genuine integer id. Coercing a malformed
            # value with int() (e.g. "05"/5.9/True) would let a corrupt row
            # admit a caller who is not really a member, turning a guessed
            # server id into a roster-enumeration oracle. The try/except only
            # caught wholly non-numeric values, not coercible-malformed ones.
            # Fail closed: only exact integer ids from live rows may satisfy
            # the gate.
            return int(user_id) in {
                uid for uid in server_members if type(uid) is int
            }
        except (TypeError, ValueError):
            return False

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

        # Resolve the server scope ONCE and fold it into the authz key. Like
        # message search, the memo must be scoped to the subject AND to the set
        # of ids the request may see: a different user — or the same user after
        # a membership change (different member set) — can never reuse an entry
        # computed under another scope. Resolution still happens before the
        # lookup (as it does for message search's readable conversations), so
        # authz is never taken from the cache.
        server_members: Optional[Set[int]] = None
        if server_id:
            server_members = self._get_server_member_ids(server_id)
            # Fail closed before any lookup/cache use: a caller outside the
            # server has no visibility into its member roster. The message
            # search path enforces the same rule via search_server_messages'
            # _can_access_server gate; server-scoped user search must not be
            # the weaker sibling.
            if not self._scope_allows_viewer(user_id, server_members):
                return []

        # Repeated identical directory queries used to replay the FTS lookup,
        # the supplemental LIKE scan, per-user server/friend hydration and
        # ranking on every call. Memoize the final page for the short TTL the
        # message/server paths already use; the key is per-user and bounded.
        scope = tuple(sorted(server_members)) if server_members is not None else ()
        cache = get_result_cache(self, "users")
        cache_key = make_key(
            "users.v1",
            (user_id, scope),
            query=query,
            server_id=server_id,
            limit=limit,
            offset=offset,
        )
        cached = cache.get(cache_key)
        if cached is not CACHE_MISS:
            return cached

        results = self._indexer.search_users(query, limit * 2, offset)

        # Apply the resolved scope to every source of results. The supplemental
        # directory fallback used to run after this filter with no scope of its
        # own, so a server-scoped search could return users who are not members
        # of that server (scope/IDOR bypass).
        if server_members is not None:
            results = [r for r in results if r.user_id in server_members]

        results = self._merge_supplemental_users(
            query, results, limit, offset=offset, allowed_user_ids=server_members
        )
        results = self._enrich_user_results(results, user_id)

        results = self._ranking_engine.rank_user_results(results, query, user_id)

        final = results[:limit]
        cache.put(cache_key, final)
        return final

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

        # Same per-user, scope-aware memo as the offset path: a repeated first
        # page (cursor=None) otherwise replays the directory scan and the
        # per-user server/friend hydration that produces mutual counts.
        server_members: Optional[Set[int]] = None
        if server_id:
            server_members = self._get_server_member_ids(server_id)
            # Same membership gate as the offset path (and message search):
            # no roster visibility for a caller outside the scope, and no
            # cache entry is written for the denied request.
            if not self._scope_allows_viewer(user_id, server_members):
                return UserSearchResultPage(results=[], next_cursor=None)

        scope = tuple(sorted(server_members)) if server_members is not None else ()
        cache = get_result_cache(self, "users_page")
        cache_key = make_key(
            "users_page.v1",
            (user_id, scope),
            query=query,
            server_id=server_id,
            limit=limit,
            cursor=cursor,
        )
        cached = cache.get(cache_key)
        if cached is not CACHE_MISS:
            return cached

        results, next_cursor = self._indexer.search_users_page(query, limit, cursor)

        # Mirror the offset path: the scope applies to the supplemental
        # directory fallback too, not just to the indexer hits.
        if server_members is not None:
            results = [r for r in results if r.user_id in server_members]

        results = self._merge_supplemental_users(
            query, results, limit, allowed_user_ids=server_members
        )
        results = self._enrich_user_results(results, user_id)
        results = self._ranking_engine.rank_user_results(results, query, user_id)

        final_page = UserSearchResultPage(results=results, next_cursor=next_cursor)
        cache.put(cache_key, final_page)
        return final_page

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
