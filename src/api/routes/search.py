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
    ServerSearchPageResponse,
)
from src.api.schemas.common import ErrorResponse
from src.api.routes.messages import _message_to_response
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

        # Return results directly from search manager
        # MessageSearchPageResponse expects results to be a list of MessageSearchResult objects
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

        return UserSearchPageResponse(
            results=page.results, next_cursor=page.next_cursor
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
