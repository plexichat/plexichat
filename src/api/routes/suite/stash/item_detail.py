"""Single-item reads, updates, pinning, deletion, and restore."""

from fastapi import Depends, HTTPException, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashItemResponse,
    StashItemUpdateRequest,
    StashPinRequest,
)
from .helpers import _error, resolve_manager as _manager
from .router import router
from .serializers import (
    _item_response,
)


@router.get(
    "/items/{item_id}",
    response_model=StashItemResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_item(
    item_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemResponse:
    try:
        return _item_response(_manager().get_item(current_user.user_id, int(item_id)))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/items/{item_id}",
    response_model=StashItemResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_item(
    item_id: str,
    body: StashItemUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemResponse:
    try:
        fields = body.model_dump(exclude_unset=True)
        if "collection_id" in fields and fields["collection_id"] is not None:
            fields["collection_id"] = int(fields["collection_id"])
        return _item_response(
            _manager().update_item(current_user.user_id, int(item_id), **fields)
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/items/{item_id}/pin",
    response_model=StashItemResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def pin_item(
    item_id: str,
    body: StashPinRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemResponse:
    try:
        return _item_response(
            _manager().set_pinned(current_user.user_id, int(item_id), body.pinned)
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_item(
    item_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_item(current_user.user_id, int(item_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/items/{item_id}/restore",
    response_model=StashItemResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def restore_item(
    item_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemResponse:
    """Restore a soft-deleted item after rechecking ownership/management access."""
    try:
        return _item_response(
            _manager().restore_item(current_user.user_id, int(item_id))
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc
