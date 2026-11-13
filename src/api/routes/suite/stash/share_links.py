"""Read-only share links and link-based downloads."""

from fastapi import Depends, HTTPException, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashDownloadTicketResponse,
    StashShareDownloadRequest,
    StashShareLinkCreateRequest,
    StashShareLinkListResponse,
    StashShareLinkResolveRequest,
    StashShareLinkResolveResponse,
    StashShareLinkResponse,
    StashShareLinkSummary,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


@router.post(
    "/items/{item_id}/share-links",
    response_model=StashShareLinkResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_share_link(
    item_id: str,
    body: StashShareLinkCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashShareLinkResponse:
    """Create an opaque, optionally password-protected read-only link."""
    try:
        link = _manager().create_share_link(
            current_user.user_id,
            int(item_id),
            password=body.password,
            permissions=body.permissions,
            expires_at=body.expires_at,
        )
        return StashShareLinkResponse.model_validate(link)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/items/{item_id}/share-links",
    response_model=StashShareLinkListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_share_links(
    item_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashShareLinkListResponse:
    try:
        links = _manager().list_share_links(current_user.user_id, int(item_id))
        return StashShareLinkListResponse(
            items=[StashShareLinkSummary.model_validate(link) for link in links]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stash item id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/share-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_share_link(
    link_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().revoke_share_link(current_user.user_id, int(link_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid share link id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/share-links/resolve",
    response_model=StashShareLinkResolveResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def resolve_share_link(
    body: StashShareLinkResolveRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashShareLinkResolveResponse:
    """Resolve a share link to its target media object (read-only)."""
    try:
        resolved = _manager().resolve_share_link(
            current_user.user_id, body.token, body.password
        )
        return StashShareLinkResolveResponse.model_validate(resolved)
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/share-links/download",
    response_model=StashDownloadTicketResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Share link does not permit download",
        },
    },
)
async def share_link_download(
    body: StashShareDownloadRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashDownloadTicketResponse:
    """Issue a download ticket to a share-link recipient (read-only).

    The share link's token + password are the recipient's authorization. The
    returned ticket is redeemed through the normal ``/download-ticket/redeem``
    endpoint, which re-validates the share link before signing a media URL.
    """
    try:
        ticket = _manager().create_share_download_ticket(
            current_user.user_id,
            body.token,
            body.password,
            int(body.version_id) if body.version_id is not None else None,
            int(body.ttl_seconds) if body.ttl_seconds is not None else 300,
        )
        return StashDownloadTicketResponse.model_validate(ticket)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid version id") from exc
    except Exception as exc:
        raise _error(exc) from exc
