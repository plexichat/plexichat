"""Item grants and download tickets."""

import time
from typing import Optional

import src.api as api
from fastapi import Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashDownloadRedeemRequest,
    StashDownloadRedeemResponse,
    StashDownloadTicketResponse,
    StashGrantRequest,
    StashGrantResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router
from .serializers import (
    _grant_response,
)


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
        return _grant_response(StashGrantResponse, grant)
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
        # Resolve and sign the media URL *before* the ticket is consumed so a
        # transient media-storage failure does not burn a one-time ticket with
        # nothing returned.
        resolved = _manager().resolve_download_ticket(current_user.user_id, body.ticket)
        media_url = None
        media_object_id = resolved.get("media_object_id")
        if media_object_id:
            media = api.get_media()
            if media is None:
                raise HTTPException(
                    status_code=503, detail="Media storage is unavailable"
                )
            # Stash stores an opaque reference. Resolve filenames first;
            # numeric IDs remain supported for existing records. Never expose
            # a storage path or unresolved opaque reference as a usable URL.
            media_file = media.get_file_by_filename(str(media_object_id))
            if media_file is None and str(media_object_id).isdigit():
                media_file = media.get_file(int(media_object_id))
            if media_file is None:
                raise HTTPException(
                    status_code=502,
                    detail="Stash media object is unavailable",
                )
            # Bound the signed URL by the ticket's remaining lifetime so a
            # redeemed ticket cannot outlive its own expiry window (sign_url
            # caps at 1h).
            remaining_ms = resolved.get("expires_at", 0)
            now_ms = int(time.time() * 1000)
            expires_in = 3600
            if remaining_ms:
                expires_in = max(
                    1,
                    min(3600, (int(remaining_ms) - now_ms) // 1000),
                )
            signed = media.sign_url(
                int(media_file.id), expires_in=expires_in, user_id=current_user.user_id
            )
            media_url = signed.url

        result = _manager().redeem_download_ticket(current_user.user_id, body.ticket)
        result["media_url"] = media_url
        # The API returns only the safe signed URL, not the opaque storage
        # reference that was used to resolve it.
        result.pop("media_object_id", None)
        return StashDownloadRedeemResponse.model_validate(result)
    except HTTPException:
        raise
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
