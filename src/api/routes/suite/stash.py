"""Authenticated PlexiStash metadata routes."""

import time
from typing import Any, Dict, Optional

import src.api as api
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashCollectionCreateRequest,
    StashCollectionListResponse,
    StashCollectionResponse,
    StashDownloadRedeemRequest,
    StashDownloadRedeemResponse,
    StashDownloadTicketResponse,
    StashGrantRequest,
    StashGrantResponse,
    StashItemCreateRequest,
    StashItemListResponse,
    StashItemResponse,
    StashItemUpdateRequest,
    StashPinRequest,
    StashVersionCreateRequest,
    StashVersionResponse,
)
from src.core.suite import stash as stash_domain
from src.core.suite.stash import StashManager
from src.core.artifacts.capabilities import CapabilityState, get_capability
from src.core.suite.stash.manager import (
    StashNotFoundError,
    StashPermissionError,
    StashValidationError,
)

router = APIRouter(prefix="/suite/stash", tags=["PlexiStash"])


def _require_stash_capability() -> None:
    """Fail closed when PlexiStash is disabled or unlicensed."""
    info = get_capability("plexistash")
    if info.state != CapabilityState.AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": 403,
                    "message": "PlexiStash is unavailable",
                    "state": info.state.value,
                }
            },
        )


def _manager() -> StashManager:
    _require_stash_capability()
    try:
        return stash_domain.get_manager()
    except RuntimeError:
        # Keep isolated route tests and embedded callers functional when they
        # provide a database without going through the full bootstrap path.
        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database unavailable")
        return StashManager(db, servers_module=api.get_servers())


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, StashNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, StashPermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, StashValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="PlexiStash operation failed")


def _item_response(item: Dict[str, Any]) -> StashItemResponse:
    return StashItemResponse.model_validate(item)


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
    search: Optional[str] = Query(None, max_length=200),
    scope: Optional[str] = Query(None, pattern="^(personal|server)$"),
    server_id: Optional[str] = Query(None),
    pinned: Optional[bool] = Query(None),
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemListResponse:
    """List metadata visible to the current user, with bounded search/paging."""
    try:
        parsed_server = int(server_id) if server_id is not None else None
        items, total = _manager().list_items(
            current_user.user_id,
            limit=limit,
            offset=offset,
            search=search,
            scope=scope,
            server_id=parsed_server,
            pinned=pinned,
        )
        return StashItemListResponse(
            items=[_item_response(item) for item in items],
            total=total,
            has_more=offset + len(items) < total,
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


@router.post(
    "/items/{item_id}/grants",
    response_model=StashGrantResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def grant_item(
    item_id: str,
    body: StashGrantRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashGrantResponse:
    try:
        grant = _manager().grant(
            current_user.user_id,
            int(item_id),
            int(body.subject_id),
            body.permissions,
            body.expires_at,
        )
        return StashGrantResponse.model_validate(grant)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid Stash item id or subject id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/items/{item_id}/download-ticket",
    response_model=StashDownloadTicketResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid item or version id"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def create_download_ticket(
    item_id: str,
    version_id: Optional[str] = Query(None),
    ttl_seconds: int = Query(300, ge=1, le=300),
    current_user: TokenInfo = Depends(get_current_user),
) -> StashDownloadTicketResponse:
    """Create a short-lived opaque ticket after checking download permission."""
    try:
        ticket = _manager().create_download_ticket(
            current_user.user_id,
            int(item_id),
            int(version_id) if version_id is not None else None,
            ttl_seconds,
        )
        return StashDownloadTicketResponse.model_validate(ticket)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid item or version id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/download-ticket/redeem",
    response_model=StashDownloadRedeemResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def redeem_download_ticket(
    body: StashDownloadRedeemRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashDownloadRedeemResponse:
    """Redeem a ticket once and recheck item authorization at use time."""
    try:
        result = _manager().redeem_download_ticket(current_user.user_id, body.ticket)
        media_url = None
        media_object_id = result.get("media_object_id")
        if media_object_id:
            try:
                media = api.get_media()
                if media is not None:
                    # Stash stores an opaque reference. Resolve filenames first;
                    # numeric IDs remain supported for existing records. Never
                    # expose a storage path or unresolved opaque reference as a
                    # usable download URL.
                    media_file = media.get_file_by_filename(str(media_object_id))
                    if media_file is None and str(media_object_id).isdigit():
                        media_file = media.get_file(int(media_object_id))
                    if media_file is not None:
                        # Bound the signed URL by the ticket's remaining
                        # lifetime so a redeemed ticket cannot outlive its own
                        # expiry window (sign_url caps at 1h).
                        remaining_ms = result.get("expires_at", 0)
                        now_ms = int(time.time() * 1000)
                        expires_in = 3600
                        if remaining_ms:
                            expires_in = max(
                                1,
                                min(
                                    3600,
                                    (int(remaining_ms) - now_ms) // 1000,
                                ),
                            )
                        signed = media.sign_url(
                            int(media_file.id), expires_in=expires_in
                        )
                        media_url = signed.url
            except Exception:
                # The ticket remains redeemed; do not expose storage internals
                # or turn a valid metadata redemption into a server error.
                media_url = None
        result["media_url"] = media_url
        # The API returns only the safe signed URL, not the opaque storage
        # reference that was used to resolve it.
        result.pop("media_object_id", None)
        return StashDownloadRedeemResponse.model_validate(result)
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_grant(
    grant_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    """Revoke a share grant; revoked grants cannot redeem future tickets."""
    try:
        _manager().revoke_grant(current_user.user_id, int(grant_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid grant id") from exc
    except Exception as exc:
        raise _error(exc) from exc


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
        )
        return StashCollectionResponse.model_validate(collection)
    except Exception as exc:
        raise _error(exc) from exc


__all__ = ["router"]
