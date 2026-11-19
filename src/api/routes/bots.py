"""
Bot management API routes.

Handles server bot approval, user bot requests, bot profiles,
bot directory browsing, and OAuth authorized application management.
"""

from typing import Optional, List, Annotated
import logging
import re
from fastapi import APIRouter, HTTPException, Depends, status, Query

import src.api as api

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from src.api.schemas.policy_types import UIText

from src.api.middleware.authentication import (
    get_current_user,
    get_optional_user,
    TokenInfo,
)

# A bot token's ``user_id`` is its human owner (the bot account id lives in
# ``account_id``). Every handler here that keys off ``current_user.user_id``
# must reject bot credentials: otherwise a leaked bot token can enumerate and
# revoke the owner's OAuth grants, toggle the owner's bots, and update their
# bot profiles. ``require_user_token`` enforces that boundary.
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.common import (
    ErrorResponse,
    NullableSnowflakeIDParam,
    SnowflakeID,
    SnowflakeIDParam,
)
from src.core import applications
from src.core.security.hardening import parse_id
from src.core.applications.exceptions import (
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    BotAlreadyApprovedError,
    BotLimitError,
    BotRequestExistsError,
    BotRequestError,
    LicenseFeatureError,
    InstallationNotFoundError,
    PermissionDeniedError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bots", tags=["Bots"])


# === Schemas ===


class ApprovedBotResponse(BaseModel):
    """Response schema for an approved bot."""

    id: SnowflakeID
    server_id: str
    application_id: str
    approved_by: str
    permissions: str
    bot_name: Optional[UIText] = None
    bot_avatar_url: Optional[str] = None
    status: str
    installed_at: int
    app_name: Optional[str] = None
    app_icon: Optional[str] = None


class BotRequestResponse(BaseModel):
    """Response schema for a bot request.

    ``id`` is a snowflake like every other identifier here, so it must be
    serialized as a decimal string (a JSON number rounds past 2**53 on the
    client). It was typed ``int``, which leaked the raw id as a number while
    the sibling ``*_id`` fields were already strings.
    """

    id: SnowflakeID
    server_id: str
    application_id: str
    requester_id: str
    reason: Optional[str] = None
    status: str
    reviewed_by: Optional[str] = None
    review_reason: Optional[str] = None
    created_at: int


class BotProfileResponse(BaseModel):
    """Response schema for a bot profile."""

    application_id: str
    description: Optional[str] = None
    short_description: Optional[str] = None
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    website_url: Optional[str] = None
    support_url: Optional[str] = None
    github_url: Optional[str] = None
    tags: List[str] = []
    nsfw: bool = False
    private: bool = False


class AuthorizedAppResponse(BaseModel):
    """Response schema for a user's authorized application."""

    id: SnowflakeID
    application_id: str
    application_name: str
    application_icon: Optional[str] = None
    scopes: List[str] = []
    authorized_at: int
    # A freshly authorized app has never been used, so ``last_used_at`` is
    # null until its first verified request. It was required, which made the
    # list endpoint 500 for every never-used grant.
    last_used_at: Optional[int] = None


# Directory text is rendered by the public client. Tags are ``UIText`` at the
# write boundary, but free-form descriptions are deliberately unrestricted and
# rows written before that policy (plus the application-description fallback,
# owned by another module) can contain tag-like text. Strip it on the way out
# so the public directory never serves executable markup regardless of how the
# stored value got there.
_HTML_TAG_LIKE = re.compile(r"<[^>]*>")


def _neutralize_markup(value):
    if not value or ("<" not in value and ">" not in value):
        return value
    return _HTML_TAG_LIKE.sub("", value)


class BotDirectoryEntry(BaseModel):
    """A bot entry in the directory listing."""

    id: SnowflakeID
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    bot_id: Optional[str] = None
    tags: List[str] = []
    nsfw: bool = False

    @field_validator("name", "description", mode="before")
    @classmethod
    def _strip_markup(cls, value):
        return _neutralize_markup(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _strip_markup_tags(cls, value):
        if not value:
            return value
        return [_neutralize_markup(tag) for tag in value]


class ApproveBotRequest(BaseModel):
    """Request to approve a bot on a server."""

    application_id: str
    permissions: str = "{}"
    bot_name: Optional[UIText] = None


class RequestBotRequest(BaseModel):
    """Request to request bot approval."""

    application_id: str
    reason: Optional[str] = None


class ReviewBotRequest(BaseModel):
    """Request to review a bot request."""

    approve: bool
    review_reason: Optional[str] = None


class UpdateBotProfileRequest(BaseModel):
    """Request to update a bot profile."""

    # Plain free text (the public directory neutralizes markup on output in
    # ``BotDirectoryEntry``). Length is still bounded; tags above use ``UIText``.
    description: Optional[str] = Field(None, max_length=2000)
    short_description: Optional[str] = Field(None, max_length=200)
    avatar_url: Optional[str] = Field(None, max_length=2048)
    banner_url: Optional[str] = Field(None, max_length=2048)
    website_url: Optional[str] = Field(None, max_length=2048)
    support_url: Optional[str] = Field(None, max_length=2048)
    github_url: Optional[str] = Field(None, max_length=2048)
    tags: Optional[List[UIText]] = None
    nsfw: Optional[bool] = None
    private: Optional[bool] = None

    @field_validator(
        "avatar_url",
        "banner_url",
        "website_url",
        "support_url",
        "github_url",
    )
    @classmethod
    def _http_url(cls, value: Optional[str]) -> Optional[str]:
        # These links are rendered in the public bot directory. A javascript:
        # or data: URL would execute for anyone who clicks the profile.
        if value is None:
            return value
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("must be an absolute http(s) URL")
        return value


class BotDirectoryResponse(BaseModel):
    """Directory listing response."""

    bots: List[BotDirectoryEntry]
    total: int
    limit: int
    offset: int


# === Helper ===


def _bot_id(value: object, field: str = "id") -> int:
    """Parse a route/body id as a snowflake, reporting bad values as 400.

    These handlers previously used bare ``int()`` on path and body strings,
    which accepted negative and arbitrarily large integers. Such values then
    flowed into application lookups and permission checks.
    """
    try:
        return parse_id(value, field)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": f"Invalid {field}"}},
        ) from exc


def _require_server_manage(user_id: int, server_id: int, action: str) -> None:
    """Fail-closed ``server.manage`` gate for server-scoped bot listings.

    ``get_approved_bots`` and ``get_bot_requests`` are plain lookups keyed only
    by ``server_id``: the core applies no authorization, so the HTTP layer is
    the only place that can stop any authenticated user from reading another
    server's bot moderation queue (which includes the requester, the submitted
    reason and the reviewer's private notes). Writing to that queue already
    requires ``server.manage``; reading it must require the same.

    A permission check that cannot be evaluated denies access (503/403) rather
    than silently letting the request through, matching the emoji and
    soundboard route gates.
    """
    servers = api.get_servers()
    if not servers:
        logger.error(f"Servers module not available for {action}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": 503, "message": "Servers module not available"}},
        )

    try:
        if hasattr(servers, "has_permission"):
            allowed = servers.has_permission(user_id, server_id, "server.manage")
        else:
            from src.core.auth.permissions import has_permission

            allowed = has_permission(
                servers.get_permissions(user_id, server_id), "server.manage"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to verify server.manage for server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )


def _coerce_nullable_int(value):
    """Coerce ``null``/empty query values for optional int params to None."""
    if (
        value is None
        or value == ""
        or (isinstance(value, str) and value.strip().lower() == "null")
    ):
        return None
    return value


NullableInt = Annotated[Optional[int], BeforeValidator(_coerce_nullable_int)]


def _approved_bot_to_response(bot, app_name=None, app_icon=None) -> ApprovedBotResponse:
    """Convert an ApprovedBot model to a response."""
    return ApprovedBotResponse(
        id=bot.id,
        server_id=str(bot.server_id),
        application_id=str(bot.application_id),
        approved_by=str(bot.approved_by),
        permissions=bot.permissions,
        bot_name=bot.bot_name,
        bot_avatar_url=bot.bot_avatar_url,
        status=bot.status.value if hasattr(bot.status, "value") else bot.status,
        installed_at=bot.installed_at,
        app_name=app_name,
        app_icon=app_icon,
    )


def _bot_request_to_response(req) -> BotRequestResponse:
    """Convert a BotRequest model to a response."""
    return BotRequestResponse(
        id=req.id,
        server_id=str(req.server_id),
        application_id=str(req.application_id),
        requester_id=str(req.requester_id),
        reason=req.reason,
        status=req.status.value if hasattr(req.status, "value") else req.status,
        reviewed_by=str(req.reviewed_by) if req.reviewed_by is not None else None,
        review_reason=req.review_reason,
        created_at=req.created_at,
    )


# === Approved Bot Endpoints ===


@router.get(
    "/servers/{server_id}/approved",
    response_model=List[ApprovedBotResponse],
    summary="List approved bots for a server",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def list_approved_bots(
    server_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get all approved bots for a server."""
    require_user_token(current_user)
    sid = _bot_id(server_id, "server_id")
    _require_server_manage(current_user.user_id, sid, "approved bot listing")
    try:
        bots = applications.get_approved_bots(server_id=sid, status="approved")
        return [_approved_bot_to_response(bot) for bot in bots]
    except Exception:
        logger.exception("Failed to list approved bots")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/servers/{server_id}/request",
    response_model=BotRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a bot for a server",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Request already exists or bot already approved",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def request_bot(
    server_id: str,
    body: RequestBotRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Request approval for a bot on a server."""
    require_user_token(current_user)
    sid = _bot_id(server_id, "server_id")
    app_id = _bot_id(body.application_id, "application_id")
    try:
        req = applications.request_bot(
            server_id=sid,
            application_id=app_id,
            requester_id=current_user.user_id,
            reason=body.reason,
        )
        return _bot_request_to_response(req)
    except BotRequestExistsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except BotAlreadyApprovedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception:
        logger.exception("Failed to request bot")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/servers/{server_id}/approved/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an approved bot from a server",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Bot not found"},
    },
)
async def remove_approved_bot(
    server_id: str,
    application_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Remove an approved bot from a server."""
    require_user_token(current_user)
    sid = _bot_id(server_id, "server_id")
    app_id = _bot_id(application_id, "application_id")
    try:
        applications.remove_approved_bot(
            server_id=sid,
            application_id=app_id,
            user_id=current_user.user_id,
        )
    except PermissionDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )
    except InstallationNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to remove approved bot")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# === Bot Disable/Enable Endpoints ===


@router.post(
    "/{bot_id}/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable a bot",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Bot not found"},
    },
)
async def disable_bot(
    bot_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Disable a bot owned by the current user."""
    require_user_token(current_user)
    from src.core import auth

    bid = _bot_id(bot_id, "bot_id")
    try:
        auth.disable_bot(owner_id=current_user.user_id, bot_id=bid)
    except Exception:
        logger.exception("Failed to disable bot")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Bot not found"}},
        )


@router.post(
    "/{bot_id}/enable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Enable a bot",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Bot not found"},
    },
)
async def enable_bot(
    bot_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Enable a bot owned by the current user."""
    require_user_token(current_user)
    from src.core import auth

    bid = _bot_id(bot_id, "bot_id")
    try:
        auth.enable_bot(owner_id=current_user.user_id, bot_id=bid)
    except Exception:
        logger.exception("Failed to enable bot")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Bot not found"}},
        )


# === Bot Request Endpoints ===


@router.get(
    "/servers/{server_id}/requests",
    response_model=List[BotRequestResponse],
    summary="List bot requests for a server",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def list_bot_requests(
    server_id: str,
    status_filter: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get bot requests for a server (server managers only)."""
    require_user_token(current_user)
    sid = _bot_id(server_id, "server_id")
    _require_server_manage(current_user.user_id, sid, "bot request listing")
    try:
        requests = applications.get_bot_requests(
            server_id=sid,
            status=status_filter,
        )
        return [_bot_request_to_response(req) for req in requests]
    except Exception:
        logger.exception("Failed to list bot requests")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/servers/{server_id}/approve",
    response_model=ApprovedBotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve a bot for a server",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Bot already approved or limit reached",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def approve_bot(
    server_id: str,
    body: ApproveBotRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Approve a bot for installation on a server."""
    require_user_token(current_user)
    sid = _bot_id(server_id, "server_id")
    app_id = _bot_id(body.application_id, "application_id")
    try:
        bot = applications.approve_bot(
            server_id=sid,
            application_id=app_id,
            approved_by=current_user.user_id,
            permissions=body.permissions,
            bot_name=body.bot_name,
        )
        return _approved_bot_to_response(bot)
    except PermissionDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )
    except BotAlreadyApprovedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except BotLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": 400,
                    "message": "Bot limit reached",
                    "max_allowed": e.max_allowed,
                    "current": e.current,
                }
            },
        )
    except LicenseFeatureError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": {
                    "code": 402,
                    "message": "Feature requires valid license",
                    "feature": e.feature,
                }
            },
        )
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Resource not found"}},
        )
    except Exception:
        logger.exception("Failed to approve bot")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.put(
    "/servers/{server_id}/requests/{request_id}",
    response_model=BotRequestResponse,
    summary="Review a bot request",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Request not found"},
    },
)
async def review_bot_request(
    server_id: str,
    request_id: SnowflakeIDParam,
    body: ReviewBotRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Review a bot request (approve or deny)."""
    require_user_token(current_user)
    sid = _bot_id(server_id, "server_id")
    try:
        req = applications.review_bot_request(
            server_id=sid,
            request_id=request_id,
            reviewer_id=current_user.user_id,
            approve=body.approve,
            review_reason=body.review_reason,
        )
        return _bot_request_to_response(req)
    except PermissionDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )
    except BotRequestError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Resource not found"}},
        )
    except Exception:
        logger.exception("Failed to review bot request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# === Bot Profile Endpoints ===


@router.get(
    "/profiles/{application_id}",
    response_model=BotProfileResponse,
    summary="Get a bot's public profile",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid application ID"},
        404: {"model": ErrorResponse, "description": "Profile not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_bot_profile(
    application_id: SnowflakeIDParam,
    current_user: Optional[TokenInfo] = Depends(get_optional_user),
):
    """Get a bot's public profile.

    A profile marked private resolves only for the application owner; for
    anyone else it is indistinguishable from a missing profile.
    """
    try:
        viewer_id = current_user.user_id if current_user else None
        profile = applications.get_bot_profile(application_id, viewer_id=viewer_id)
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Bot profile not found"}},
            )
        return BotProfileResponse(
            application_id=str(profile.application_id),
            description=profile.description,
            short_description=profile.short_description,
            avatar_url=profile.avatar_url,
            banner_url=profile.banner_url,
            website_url=profile.website_url,
            support_url=profile.support_url,
            github_url=profile.github_url,
            tags=profile.tags,
            nsfw=profile.nsfw,
            private=profile.private,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get bot profile")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.put(
    "/profiles/{application_id}",
    response_model=BotProfileResponse,
    summary="Update a bot's profile",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Application not found"},
    },
)
async def update_bot_profile(
    application_id: str,
    body: UpdateBotProfileRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Update a bot's public profile."""
    require_user_token(current_user)
    app_id = _bot_id(application_id, "application_id")
    try:
        profile = applications.update_bot_profile(
            application_id=app_id,
            user_id=current_user.user_id,
            description=body.description,
            short_description=body.short_description,
            avatar_url=body.avatar_url,
            banner_url=body.banner_url,
            website_url=body.website_url,
            support_url=body.support_url,
            github_url=body.github_url,
            tags=body.tags,
            nsfw=body.nsfw,
            private=body.private,
        )
        return BotProfileResponse(
            application_id=str(profile.application_id),
            description=profile.description,
            short_description=profile.short_description,
            avatar_url=profile.avatar_url,
            banner_url=profile.banner_url,
            website_url=profile.website_url,
            support_url=profile.support_url,
            github_url=profile.github_url,
            tags=profile.tags,
            nsfw=profile.nsfw,
            private=profile.private,
        )
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Resource not found"}},
        )
    except ApplicationAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )
    except Exception:
        logger.exception("Failed to update bot profile")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# === Bot Directory Endpoints ===


@router.get(
    "/directory",
    response_model=BotDirectoryResponse,
    summary="Browse the bot directory",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server_id"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_bot_directory(
    server_id: Annotated[
        NullableSnowflakeIDParam,
        Query(description="Filter out bots already approved on this server"),
    ] = None,
    q: Optional[str] = Query(None, max_length=256, description="Search query"),
    tag: Optional[str] = Query(None, max_length=64, description="Filter by tag"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    offset: int = Query(0, ge=0, le=1000000, description="Number of results to skip"),
):
    """Browse available bots in the directory."""
    try:
        if limit > 100:
            limit = 100
        result = applications.get_bot_directory(
            server_id=server_id,
            include_public=True,
            limit=limit,
            offset=offset,
            q=q,
            tag=tag,
        )
        entries = [
            BotDirectoryEntry(
                **{
                    **bot,
                    "bot_id": str(bot["bot_id"])
                    if bot.get("bot_id") is not None
                    else None,
                }
            )
            for bot in result["bot_list"]
        ]
        return BotDirectoryResponse(
            bots=entries,
            total=result["total"],
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.exception("Failed to list bot directory")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# === Authorized Apps Endpoints ===


@router.get(
    "/authorized-apps",
    response_model=List[AuthorizedAppResponse],
    summary="Get user's authorized OAuth applications",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_authorized_apps(
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get all OAuth2 applications the current user has authorized."""
    require_user_token(current_user)
    try:
        apps = applications.get_user_authorized_apps(current_user.user_id)
        return [
            AuthorizedAppResponse(
                id=SnowflakeID(app.id),
                application_id=str(app.application_id),
                application_name=app.application_name,
                application_icon=app.application_icon,
                scopes=app.scopes,
                authorized_at=app.authorized_at,
                last_used_at=app.last_used_at,
            )
            for app in apps
        ]
    except Exception:
        logger.exception("Failed to list authorized apps")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/authorized-apps/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an authorized application",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Authorized app not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_authorized_app(
    token_id: SnowflakeIDParam,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Revoke an authorized application's access."""
    require_user_token(current_user)
    try:
        applications.revoke_authorized_app(
            token_id=token_id,
            user_id=current_user.user_id,
        )
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Resource not found"}},
        )
    except Exception:
        logger.exception("Failed to revoke authorized app")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
