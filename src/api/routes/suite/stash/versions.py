"""Stash item version listing and creation."""

from fastapi import Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashVersionCreateRequest,
    StashVersionResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


@router.get(
    "/items/{item_id}/versions",
    response_model=list[StashVersionResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_versions(
    item_id: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
) -> list[StashVersionResponse]:
    try:
        versions = _manager().list_versions(current_user.user_id, int(item_id), limit)
        return [StashVersionResponse.model_validate(version) for version in versions]
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/items/{item_id}/versions",
    response_model=StashVersionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_version(
    item_id: str,
    body: StashVersionCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashVersionResponse:
    try:
        version = _manager().create_version(
            current_user.user_id, int(item_id), **body.model_dump()
        )
        return StashVersionResponse.model_validate(version)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc
