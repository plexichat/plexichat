"""Stash item listing and creation."""

from typing import Optional

from fastapi import Depends, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse, NullableSnowflakeIDParam
from src.api.schemas.stash import (
    StashItemCreateRequest,
    StashItemListResponse,
    StashItemResponse,
)
from src.core.suite.stash.manager import (
    StashValidationError,
)
from .helpers import _error, resolve_manager as _manager
from .router import router
from .serializers import (
    _item_response,
    _items_response,
)


@router.get(
    "/items",
    response_model=StashItemListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_items(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    cursor: Optional[str] = Query(None, max_length=32),
    search: Optional[str] = Query(None, max_length=200),
    scope: Optional[str] = Query(None, pattern="^(personal|server)$"),
    server_id: Optional[str] = Query(None),
    collection_id: NullableSnowflakeIDParam = None,
    pinned: Optional[bool] = Query(None),
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemListResponse:
    """List metadata visible to the current user, with bounded search/paging.

    ``cursor`` is the opaque ``next_cursor``-style offset issued by the
    manager's cursor pagination; when present it overrides ``offset`` so
    clients can walk more than one page with no silent truncation.
    """
    try:
        parsed_server = int(server_id) if server_id is not None else None
        if cursor is not None:
            items, total, next_cursor = _manager().list_items_page(
                current_user.user_id,
                limit=limit,
                cursor=cursor,
                search=search,
                scope=scope,
                server_id=parsed_server,
                pinned=pinned,
                collection_id=collection_id,
            )
            has_more = next_cursor is not None
        else:
            items, total = _manager().list_items(
                current_user.user_id,
                limit=limit,
                offset=offset,
                search=search,
                scope=scope,
                server_id=parsed_server,
                pinned=pinned,
                collection_id=collection_id,
            )
            has_more = offset + len(items) < total
        return _items_response(
            items,
            total=total,
            has_more=has_more,
        )
    except (ValueError, StashValidationError) as exc:
        raise _error(exc) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/items",
    response_model=StashItemResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_item(
    body: StashItemCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemResponse:
    """Create a Stash item without accepting a filesystem path."""
    try:
        item = _manager().create_item(
            current_user.user_id,
            item_type=body.item_type,
            title=body.title,
            scope=body.scope,
            server_id=int(body.server_id) if body.server_id is not None else None,
            description=body.description,
            collection_id=int(body.collection_id)
            if body.collection_id is not None
            else None,
            media_object_id=body.media_object_id,
            metadata=body.metadata,
            tags=body.tags,
        )
        return _item_response(item)
    except Exception as exc:
        raise _error(exc) from exc
