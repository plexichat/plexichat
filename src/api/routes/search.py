"""
Search routes - Unified search and discovery endpoints.
"""

import copy
import time
from collections import OrderedDict
from dataclasses import asdict
from typing import Any, List, Optional, Tuple
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.core.events.snowflakes import stringify_snowflake_ids
from src.api.schemas.search import (
    MessageSearchPageResponse,
    UserSearchPageResponse,
    UserSearchResultResponse,
    ServerSearchPageResponse,
)
from src.api.schemas.common import ErrorResponse
from src.core.search.exceptions import SearchError, SearchBackendError
from src.core.security.hardening import HardeningViolation, parse_id


import utils.logger as logger

router = APIRouter(prefix="/search", tags=["Search"])

#: Opaque pagination cursors are short signed tokens; never accept an
#: unbounded blob as a cursor key.
MAX_SEARCH_CURSOR_LENGTH = 512

#: Snowflake ids are at most 19 decimal digits. Bounding the raw text first
#: stops a hostile query parameter from making ``int`` churn on a huge blob.
MAX_SEARCH_ID_LENGTH = 32


def _parse_search_id(raw: str, field: str) -> int:
    """Parse an id filter as a positive snowflake, not an arbitrary integer.

    Bare ``int()`` accepted negatives, zero, values past 2**63-1 and exotic
    forms (``1_2``, ``+12``). Those then flowed into backend lookup queries as
    though they were real conversation/user/channel ids. Route every id through
    the shared snowflake guard and surface a ValueError so the endpoint's
    existing 400 contract applies before any search backend access.
    """
    if len(raw) > MAX_SEARCH_ID_LENGTH or not raw.isdigit():
        raise ValueError(f"{field} is not a valid snowflake")
    try:
        return parse_id(raw, field)
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc


# Autocomplete fires on every keystroke, so the same (user, prefix) is asked
# repeatedly within seconds. Each miss runs a filter scan plus a GROUP BY over
# the user's search history. Memoize recent answers for a short TTL to bound
# that work; the cache is size-capped and keyed only on the request inputs.
# Keys carry the authenticated user id, so one credential can never read
# another's suggestions, and the route runs its auth gate plus the per-user
# rate limit before consulting the cache.
_SUGGESTION_CACHE_TTL_SECONDS = 2.0
_SUGGESTION_CACHE_MAX_ENTRIES = 256
_suggestion_cache: "OrderedDict[Tuple[int, str, int], Tuple[float, List[str]]]" = (
    OrderedDict()
)


def _clear_suggestion_cache() -> None:
    """Drop memoized suggestions (tests / explicit invalidation)."""
    _suggestion_cache.clear()


#: Autocomplete fires on every keystroke, but the memoization above only
#: collapses *identical* prefixes. A caller can cycle through distinct
#: prefixes so every request misses and runs a GROUP BY over their whole
#: search history — an unbounded read amplifier. Bound requests per user in
#: the shared cache tier (300/min tolerates even very fast typing).
SUGGESTION_RATE_LIMIT = 300


def _check_suggestion_rate_limit(user_id: int) -> None:
    """Throttle autocomplete; fail open if the cache tier is unavailable."""
    try:
        from src.core.database import check_rate_limit

        allowed, _remaining = check_rate_limit(
            f"search:suggestions:{user_id}", SUGGESTION_RATE_LIMIT, 60
        )
    except Exception as exc:
        logger.debug(f"Suggestion rate-limit unavailable: {exc}")
        return
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": {"code": 429, "message": "Too many requests"}},
        )


def _suggestions(search_mod, user_id: int, q: str, limit: int) -> List[str]:
    """Return suggestions, computing at most once per key per TTL window.

    The backing store is a bounded LRU. Reaching the cap evicts only the
    least-recently-used key and expired keys are pruned first, so a burst of
    distinct prefixes does not discard every other user's still-fresh answer
    and force a fresh GROUP BY for each of them.
    """
    key = (user_id, q, limit)
    now = time.monotonic()
    cached = _suggestion_cache.get(key)
    if cached is not None:
        if (now - cached[0]) < _SUGGESTION_CACHE_TTL_SECONDS:
            _suggestion_cache.move_to_end(key)
            return cached[1]
        # Expired entries are never served; drop this one before recomputing.
        _suggestion_cache.pop(key, None)

    suggestions = search_mod.get_search_suggestions(
        user_id=user_id,
        partial_query=q,
        limit=limit,
    )

    # Reclaim expired keys before any live entry so staleness cannot consume
    # the capacity reserved for hot answers. TTLs are short (seconds), so there
    # are at most a few hundred entries to inspect.
    for stale in [
        k
        for k, (stored, _) in _suggestion_cache.items()
        if (now - stored) >= _SUGGESTION_CACHE_TTL_SECONDS
    ]:
        _suggestion_cache.pop(stale, None)
    # One-at-a-time LRU eviction keeps the rest of the working set warm.
    while len(_suggestion_cache) >= _SUGGESTION_CACHE_MAX_ENTRIES:
        _suggestion_cache.popitem(last=False)

    _suggestion_cache[key] = (now, suggestions)
    return suggestions


@router.get(
    "/messages",
    response_model=MessageSearchPageResponse,
    summary="Search messages",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def search_messages(
    query: str = Query(..., max_length=256, description="Search query"),
    conversation_id: Optional[str] = Query(
        None, max_length=32, description="Limit to conversation"
    ),
    server_id: Optional[str] = Query(
        None, max_length=32, description="Limit to server"
    ),
    channel_id: Optional[str] = Query(
        None, max_length=32, description="Limit to channel"
    ),
    author_id: Optional[str] = Query(
        None, max_length=32, description="Limit to author"
    ),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(
        None, max_length=MAX_SEARCH_CURSOR_LENGTH, description="Cursor for pagination"
    ),
    current_user: TokenInfo = Depends(get_current_user),
) -> MessageSearchPageResponse:
    """
    Search messages across accessible conversations.
    Supports advanced query filters (e.g., from:user, has:image).
    """
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")

    try:
        # Convert IDs to validated snowflakes before any lookup
        conv_id = (
            _parse_search_id(conversation_id, "conversation_id")
            if conversation_id
            else None
        )
        srv_id = _parse_search_id(server_id, "server_id") if server_id else None
        chan_id = _parse_search_id(channel_id, "channel_id") if channel_id else None
        auth_id = _parse_search_id(author_id, "author_id") if author_id else None

        page = search_mod.search_messages_page(
            user_id=current_user.user_id,
            query=query,
            conversation_id=conv_id,
            server_id=srv_id,
            channel_id=chan_id,
            author_id=auth_id,
            limit=limit,
            cursor=cursor,
        )

        # Search hits are core ``MessageSearchResult`` records, which carry
        # ``conversation_id`` (and a null ``channel_id`` for DMs). Coerce them
        # through the shared message serializer so ``channel_id`` falls back
        # to the conversation id; coercing the raw record directly left
        # ``channel_id`` null and raised a validation error, turning every DM
        # search into a 400.
        from src.api.routes.messages.helpers import (
            _message_to_response,
            batch_embed_suppressed_ids,
            batch_poll_results,
        )

        # Resolve embed suppression for the whole page in ONE query. Without
        # this, `_message_to_response` falls back to a per-message
        # `are_embeds_suppressed()` lookup, so a single 100-hit search issued
        # 100 near-identical SQL queries just to learn that none were
        # suppressed. The batch helper is bounded (chunked IN clauses) and
        # falls back to the exact per-id semantics if the batch query is
        # unavailable, so suppression can never be silently dropped.
        # Suppression is a property of the message, not of the viewer, so
        # folding it into the response does not weaken the (already applied)
        # per-user search authorization or introduce a cross-user leak.
        suppressed_ids = batch_embed_suppressed_ids(
            [getattr(result, "id", None) for result in page.results],
            embeds_mod=api.get_embeds(),
        )

        # One bounded poll hydration for the whole hit set (viewer-scoped):
        # without this, every poll-bearing hit ran its own get_results() query
        # storm inside the serializer.
        poll_results_map = batch_poll_results(
            page.results, current_user.user_id, polls_mod=api.get_polls()
        )

        results = [
            _message_to_response(
                result,
                viewer_user_id=current_user.user_id,
                suppress_embeds=getattr(result, "id", None) in suppressed_ids,
                poll_results_data=poll_results_map,
            )
            for result in page.results
        ]

        return MessageSearchPageResponse(results=results, next_cursor=page.next_cursor)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except SearchBackendError as e:
        logger.error(f"Search backend unavailable (messages): {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "search_backend_unavailable",
                "message": "Internal server error",
            },
        )
    except SearchError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(f"Search messages failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


@router.get(
    "/users",
    response_model=UserSearchPageResponse,
    summary="Search users",
)
async def search_users(
    query: str = Query(..., max_length=256, description="Username or display name"),
    server_id: Optional[str] = Query(
        None, max_length=32, description="Limit to server members"
    ),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(
        None, max_length=MAX_SEARCH_CURSOR_LENGTH, description="Cursor for pagination"
    ),
    current_user: TokenInfo = Depends(get_current_user),
) -> UserSearchPageResponse:
    """Search for users across the platform or within a specific server."""
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")

    try:
        srv_id = _parse_search_id(server_id, "server_id") if server_id else None

        page = search_mod.search_users_page(
            user_id=current_user.user_id,
            query=query,
            server_id=srv_id,
            limit=limit,
            cursor=cursor,
        )

        # Bulk-resolve profiles once per request so each result carries a
        # fresh username and avatar URL (no N+1 lookups).
        profiles: dict = {}
        auth = api.get_auth()
        if auth is not None and page.results:
            try:
                profiles = (
                    auth.get_user_profiles_bulk([r.user_id for r in page.results]) or {}
                )
            except Exception as profile_error:
                logger.debug(f"User search profile resolution failed: {profile_error}")

        items = []
        for r in page.results:
            profile = profiles.get(str(r.user_id), {})
            items.append(
                UserSearchResultResponse(
                    id=r.user_id,
                    user_id=r.user_id,
                    # Prefer the live directory username over a stale index entry.
                    username=profile.get("username") or r.username or "",
                    display_name=r.display_name,
                    avatar_url=r.avatar_url or profile.get("avatar_url"),
                    score=r.score,
                )
            )
        return UserSearchPageResponse(
            items=items,
            total=len(items),
            results=items,
            next_cursor=page.next_cursor,
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except SearchBackendError as e:
        logger.error(f"Search backend unavailable (users): {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "search_backend_unavailable",
                "message": "Internal server error",
            },
        )
    except SearchError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(f"Search users failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


@router.get(
    "/servers",
    response_model=ServerSearchPageResponse,
    summary="Search public servers",
)
async def search_servers(
    query: str = Query(..., max_length=256, description="Name, description, or tags"),
    category: Optional[str] = Query(
        None, max_length=64, description="Filter by category"
    ),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(
        None, max_length=MAX_SEARCH_CURSOR_LENGTH, description="Cursor for pagination"
    ),
    current_user: TokenInfo = Depends(get_current_user),
) -> ServerSearchPageResponse:
    """Search for public servers to join."""
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")

    try:
        page = search_mod.search_servers_page(
            user_id=current_user.user_id,
            query=query,
            category=category,
            limit=limit,
            cursor=cursor,
        )

        return ServerSearchPageResponse(
            results=page.results, next_cursor=page.next_cursor
        )
    except SearchBackendError as e:
        logger.error(f"Search backend unavailable (servers): {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "search_backend_unavailable",
                "message": "Internal server error",
            },
        )
    except SearchError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(f"Search servers failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


@router.get(
    "/health",
    summary="Search health",
)
async def search_health(
    current_user: TokenInfo = Depends(get_current_user),
):
    """Search backend health + stats for Docker/client diagnostics."""
    search_mod = api.get_search()
    if not search_mod:
        return {"enabled": False, "backend": None, "indexer": None, "healthy": False}
    try:
        stats = {}
        backend = None
        indexer_name = None
        healthy = False
        try:
            backend = (
                search_mod._config.get("backend")
                if hasattr(search_mod, "_config")
                else None
            )
        except Exception:
            backend = None
        try:
            indexer_name = (
                type(search_mod._manager._indexer).__name__
                if hasattr(search_mod, "_manager")
                and hasattr(search_mod._manager, "_indexer")
                else type(search_mod._indexer).__name__
                if hasattr(search_mod, "_indexer")
                else None
            )
            # fallback: directly on module
            if not indexer_name and hasattr(search_mod, "get_manager"):
                try:
                    mgr = search_mod.get_manager()
                    indexer_name = type(mgr._indexer).__name__
                    backend = mgr._config.get("backend", backend)
                except Exception as config_error:
                    logger.debug(f"search backend config probe failed: {config_error}")
        except Exception as manager_error:
            # The manager is optional telemetry; search itself still runs.
            logger.debug(f"search manager introspection failed: {manager_error}")
        try:
            # try to get stats via manager
            mgr = (
                search_mod.get_manager() if hasattr(search_mod, "get_manager") else None
            )
            if mgr and hasattr(mgr, "_indexer") and hasattr(mgr._indexer, "get_stats"):
                stats = mgr._indexer.get_stats() or {}
            elif hasattr(search_mod, "_indexer") and hasattr(
                search_mod._indexer, "get_stats"
            ):
                stats = search_mod._indexer.get_stats() or {}
        except Exception:
            stats = {}
        try:
            # health_check
            mgr = (
                search_mod.get_manager() if hasattr(search_mod, "get_manager") else None
            )
            if (
                mgr
                and hasattr(mgr, "_indexer")
                and hasattr(mgr._indexer, "health_check")
            ):
                healthy = bool(mgr._indexer.health_check())
            elif hasattr(search_mod, "_indexer") and hasattr(
                search_mod._indexer, "health_check"
            ):
                healthy = bool(search_mod._indexer.health_check())
            else:
                healthy = True
        except Exception:
            healthy = False
        return {
            "enabled": True,
            "backend": backend,
            "indexer": indexer_name,
            "healthy": healthy,
            "stats": stats,
        }
    except Exception as e:
        logger.error(f"Search health failed: {e}", exc_info=True)
        # Do not reflect the raw exception: it can carry backend hostnames,
        # credentials or query text into a response any authenticated user
        # can read. Keep the diagnostic shape, omit the detail.
        return {"enabled": True, "healthy": False}


@router.get(
    "/suggestions",
    summary="Get search suggestions",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        429: {"model": ErrorResponse, "description": "Too many requests"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_search_suggestions(
    q: str = Query(..., max_length=128, description="Partial query for suggestions"),
    limit: int = Query(default=10, ge=1, le=20),
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get search suggestions for autocomplete (filter names + history)."""
    # A bot token's user_id is its human owner (the bot id lives in
    # account_id). The rate-limit bucket and the suggestion cache below are
    # both keyed on that id, so without this gate a bot credential would spend
    # the owner's search:suggestions quota, evade its own, and read the
    # owner's autocomplete history. Reject bots before either lookup.
    require_user_token(current_user)
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")
    # Outside the try below: a throttle 429 must never be rewrapped as a 500.
    _check_suggestion_rate_limit(current_user.user_id)
    try:
        suggestions = _suggestions(search_mod, current_user.user_id, q, limit)
        return {"suggestions": suggestions}
    except SearchBackendError as e:
        logger.error(f"Search backend unavailable (suggestions): {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "search_backend_unavailable",
                "message": "Internal server error",
            },
        )
    except Exception as e:
        logger.error(f"Search suggestions failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


@router.get(
    "/discovery/servers",
    summary="List public servers (discovery)",
)
async def list_public_servers(
    category: Optional[str] = Query(
        None, max_length=64, description="Filter by category"
    ),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
    current_user: TokenInfo = Depends(get_current_user),
):
    """List public servers for discovery."""
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")
    try:
        results = search_mod.list_public_servers(
            category=category,
            limit=limit,
            offset=offset,
        )
        # ServerListing is a core dataclass whose ``id``/``server_id`` are
        # integer snowflakes. FastAPI would serialize those as JSON numbers,
        # which a JS client rounds above 2^53; emit them as decimal strings to
        # match the SnowflakeID convention the search schemas already follow.
        listings = [stringify_snowflake_ids(asdict(listing)) for listing in results]
        return {"results": listings, "next_cursor": None}
    except Exception as e:
        logger.error(f"List public servers failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


# Discovery categories are platform-wide (not user-scoped), so a single entry
# suffices. Rebuilding them runs a GROUP BY over the whole server-listing table
# via ``update_category_counts`` on every request; a burst of catalogue reads
# turns into repeated full scans. Memoize the computed list briefly. The short
# TTL is the invalidation window for listing changes, and the helper lets
# writers/tests drop it explicitly when a listing is (un)listed.
_CATEGORIES_CACHE_TTL_SECONDS = 2.0
_categories_cache: Optional[Tuple[float, List[Any]]] = None


def _clear_categories_cache() -> None:
    """Drop memoized discovery categories (tests / explicit invalidation)."""
    global _categories_cache
    _categories_cache = None


def _server_categories(search_mod) -> List[Any]:
    """Return discovery categories, recomputing at most once per TTL window.

    Returned rows are deep copies: callers (and the response serializer) must
    never be able to mutate the memoized ``ServerCategory`` objects, which
    would corrupt both this cache and the search manager's own cache.
    """
    global _categories_cache
    now = time.monotonic()
    if _categories_cache is not None:
        stored_at, cached = _categories_cache
        if (now - stored_at) < _CATEGORIES_CACHE_TTL_SECONDS:
            return copy.deepcopy(cached)
    fresh = search_mod.get_server_categories()
    _categories_cache = (now, fresh)
    return copy.deepcopy(fresh)


@router.get(
    "/discovery/categories",
    summary="Get server discovery categories",
)
async def get_server_categories(
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get available server categories for discovery."""
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")
    try:
        categories = _server_categories(search_mod)
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Get categories failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")
