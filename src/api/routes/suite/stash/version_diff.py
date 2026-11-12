"""Structured diffs between stored versions."""

from typing import Optional

from fastapi import Depends, HTTPException, Query

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashVersionDiffResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


@router.get(
    "/items/{item_id}/versions/diff",
    response_model=StashVersionDiffResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def diff_versions(
    item_id: str,
    a: str = Query(..., description="Base version id"),
    b: Optional[str] = Query(
        None, description="Compare version id (defaults to current)"
    ),
    current_user: TokenInfo = Depends(get_current_user),
) -> StashVersionDiffResponse:
    """Return a structured diff between two stored versions."""
    try:
        diff = _manager().diff_versions(
            current_user.user_id,
            int(item_id),
            int(a),
            int(b) if b is not None else None,
        )
        return StashVersionDiffResponse.model_validate(diff)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid item or version id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc
