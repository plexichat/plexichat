"""Collections and collection grants."""

from typing import Optional

from fastapi import Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashCollectionCreateRequest,
    StashCollectionGrantListResponse,
    StashCollectionGrantRequest,
    StashCollectionGrantResponse,
    StashCollectionListResponse,
    StashCollectionResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router
from .serializers import (
    _attach_grant_profile,
    _grant_profile_cache,
    _grant_response,
)


@router.get(
    "/collections",
    response_model=StashCollectionListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_collections(
    server_id: Optional[str] = Query(None),
    current_user: TokenInfo = Depends(get_current_user),
) -> StashCollectionListResponse:
    try:
        parsed_server = int(server_id) if server_id is not None else None
        collections = _manager().list_collections(current_user.user_id, parsed_server)
        return StashCollectionListResponse(
            items=[
                StashCollectionResponse.model_validate(collection)
                for collection in collections
            ]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid server id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/collections",
    response_model=StashCollectionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_collection(
    body: StashCollectionCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashCollectionResponse:
    try:
        collection = _manager().create_collection(
            current_user.user_id,
            body.name,
            int(body.server_id) if body.server_id is not None else None,
            int(body.parent_id) if body.parent_id is not None else None,
        )
        return StashCollectionResponse.model_validate(collection)
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/collections/{collection_id}/grants",
    response_model=StashCollectionGrantResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def grant_collection(
    collection_id: str,
    body: StashCollectionGrantRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashCollectionGrantResponse:
    """Share a collection; contained items inherit the grant."""
    try:
        grant = _manager().grant_collection(
            current_user.user_id,
            int(collection_id),
            int(body.subject_id),
            body.permissions,
            body.expires_at,
        )
        return _grant_response(StashCollectionGrantResponse, grant)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid collection id or subject id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/collections/{collection_id}/grants",
    response_model=StashCollectionGrantListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_collection_grants(
    collection_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashCollectionGrantListResponse:
    try:
        grants = _manager().list_collection_grants(
            current_user.user_id, int(collection_id)
        )
        cache = _grant_profile_cache([g.get("subject_id") for g in grants])
        return StashCollectionGrantListResponse(
            items=[
                StashCollectionGrantResponse.model_validate(
                    _attach_grant_profile(grant, cache)
                )
                for grant in grants
            ]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid collection id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/collection-grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_collection_grant(
    grant_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().revoke_collection_grant(current_user.user_id, int(grant_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid collection grant id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc
