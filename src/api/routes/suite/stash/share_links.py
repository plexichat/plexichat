"""Read-only share links and link-based downloads."""

import hashlib

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
from src.core.database import check_rate_limit

from .helpers import _error, parse_stash_id, resolve_manager as _manager
from .router import router

# A share-link token is a bearer credential: whoever holds it may attempt the
# optional password as often as the process-wide user/IP limiter allows (its
# default is thousands of requests per user), which makes a short password
# brute-forceable and reduces the password to decorative security. Bound the
# VERIFY attempts per link, independently of the global limiter, so a guessed
# token cannot be turned into a content-disclosure primitive by password
# guessing. The same gate covers metadata resolution and ticket issuance, the
# two callers of the shared password check in the Stash manager.
_SHARE_LINK_VERIFY_LIMIT = 10
_SHARE_LINK_VERIFY_WINDOW_SECONDS = 60


def _share_link_verify_key(token: str) -> str:
    """Return a non-reversible rate-limit key for a share-link token.

    The token is itself the credential, so it must never be written into the
    rate-limit store (Valkey or the in-process fallback) where operators, log
    shippers or a cache dump could read it. Only its SHA-256 fingerprint is
    retained, matching the manager's stored ``token_hash``.
    """
    fingerprint = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    return f"stash:share-link-verify:{fingerprint}"


def _enforce_share_link_verify_limit(token: str) -> None:
    """Refuse excessive password guesses against one share link."""
    allowed, _remaining = check_rate_limit(
        _share_link_verify_key(token),
        _SHARE_LINK_VERIFY_LIMIT,
        _SHARE_LINK_VERIFY_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"code": 429, "message": "Too many share link attempts"}},
        )


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
            parse_stash_id(item_id, "item id"),
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
        links = _manager().list_share_links(
            current_user.user_id, parse_stash_id(item_id, "item id")
        )
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
        _manager().revoke_share_link(
            current_user.user_id, parse_stash_id(link_id, "share link id")
        )
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
    # Gate BEFORE the manager's password check so wrong guesses consume the
    # budget too; otherwise only successful logins would be counted.
    _enforce_share_link_verify_limit(body.token)
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
    # Same password-guess budget as metadata resolution: both paths call the
    # manager's shared token+password validator.
    _enforce_share_link_verify_limit(body.token)
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
