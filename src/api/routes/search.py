"""
Search routes - Unified search and discovery endpoints.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.search import (
    MessageSearchPageResponse,
    UserSearchPageResponse,
    UserSearchResultResponse,
    ServerSearchPageResponse,
)
from src.api.schemas.common import ErrorResponse
from src.core.search.exceptions import SearchError


import utils.logger as logger

router = APIRouter(prefix="/search", tags=["Search"])


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
    query: str = Query(..., description="Search query"),
    conversation_id: Optional[str] = Query(None, description="Limit to conversation"),
    server_id: Optional[str] = Query(None, description="Limit to server"),
    channel_id: Optional[str] = Query(None, description="Limit to channel"),
    author_id: Optional[str] = Query(None, description="Limit to author"),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
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
        # Convert IDs to int
        conv_id = int(conversation_id) if conversation_id else None
        srv_id = int(server_id) if server_id else None
        chan_id = int(channel_id) if channel_id else None
        auth_id = int(author_id) if author_id else None

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

        return MessageSearchPageResponse(
            results=page.results, next_cursor=page.next_cursor
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SearchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Search messages failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


@router.get(
    "/users",
    response_model=UserSearchPageResponse,
    summary="Search users",
)
async def search_users(
    query: str = Query(..., description="Username or display name"),
    server_id: Optional[str] = Query(None, description="Limit to server members"),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    current_user: TokenInfo = Depends(get_current_user),
) -> UserSearchPageResponse:
    """Search for users across the platform or within a specific server."""
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")

    try:
        srv_id = int(server_id) if server_id else None

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
    except SearchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Search users failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


@router.get(
    "/servers",
    response_model=ServerSearchPageResponse,
    summary="Search public servers",
)
async def search_servers(
    query: str = Query(..., description="Name, description, or tags"),
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
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
    except SearchError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
                except Exception:
                    pass
        except Exception:
            pass
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
        import utils.logger as logger

        logger.error(f"Search health failed: {e}", exc_info=True)
        return {"enabled": True, "healthy": False, "error": str(e)}


@router.get(
    "/suggestions",
    summary="Get search suggestions",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_search_suggestions(
    q: str = Query(..., description="Partial query for suggestions"),
    limit: int = Query(default=10, ge=1, le=20),
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get search suggestions for autocomplete (filter names + history)."""
    search_mod = api.get_search()
    if not search_mod:
        raise HTTPException(status_code=500, detail="Search module not available")
    try:
        suggestions = search_mod.get_search_suggestions(
            user_id=current_user.user_id,
            partial_query=q,
            limit=limit,
        )
        return {"suggestions": suggestions}
    except Exception as e:
        logger.error(f"Search suggestions failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


@router.get(
    "/discovery/servers",
    summary="List public servers (discovery)",
)
async def list_public_servers(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
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
        return {"results": results, "next_cursor": None}
    except Exception as e:
        logger.error(f"List public servers failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")


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
        categories = search_mod.get_server_categories()
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Get categories failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal search error")
