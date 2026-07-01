"""
Bot management API routes.

Handles server bot approval, user bot requests, bot profiles,
bot directory browsing, and OAuth authorized application management.
"""

from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, status

from pydantic import BaseModel

from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core import applications
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


router = APIRouter(prefix="/bots", tags=["Bots"])


# === Schemas ===


class ApprovedBotResponse(BaseModel):
    """Response schema for an approved bot."""

    id: int
    server_id: int
    application_id: int
    approved_by: int
    permissions: str
    bot_name: Optional[str] = None
    bot_avatar_url: Optional[str] = None
    status: str
    installed_at: int
    app_name: Optional[str] = None
    app_icon: Optional[str] = None


class BotRequestResponse(BaseModel):
    """Response schema for a bot request."""

    id: int
    server_id: int
    application_id: int
    requester_id: int
    reason: Optional[str] = None
    status: str
    reviewed_by: Optional[int] = None
    review_reason: Optional[str] = None
    created_at: int


class BotProfileResponse(BaseModel):
    """Response schema for a bot profile."""

    application_id: int
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

    id: int
    application_id: int
    application_name: str
    application_icon: Optional[str] = None
    scopes: List[str] = []
    authorized_at: int
    last_used_at: int


class BotDirectoryEntry(BaseModel):
    """A bot entry in the directory listing."""

    id: int
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    bot_id: Optional[int] = None
    tags: List[str] = []
    nsfw: bool = False


class ApproveBotRequest(BaseModel):
    """Request to approve a bot on a server."""

    application_id: int
    permissions: str = "{}"
    bot_name: Optional[str] = None


class RequestBotRequest(BaseModel):
    """Request to request bot approval."""

    application_id: int
    reason: Optional[str] = None


class ReviewBotRequest(BaseModel):
    """Request to review a bot request."""

    approve: bool
    review_reason: Optional[str] = None


class UpdateBotProfileRequest(BaseModel):
    """Request to update a bot profile."""

    description: Optional[str] = None
    short_description: Optional[str] = None
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    website_url: Optional[str] = None
    support_url: Optional[str] = None
    github_url: Optional[str] = None
    tags: Optional[List[str]] = None
    nsfw: Optional[bool] = None
    private: Optional[bool] = None


class BotDirectoryResponse(BaseModel):
    """Directory listing response."""

    bots: List[BotDirectoryEntry]
    total: int
    limit: int
    offset: int


# === Helper ===


def _approved_bot_to_response(bot, app_name=None, app_icon=None) -> ApprovedBotResponse:
    """Convert an ApprovedBot model to a response."""
    return ApprovedBotResponse(
        id=bot.id,
        server_id=bot.server_id,
        application_id=bot.application_id,
        approved_by=bot.approved_by,
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
        server_id=req.server_id,
        application_id=req.application_id,
        requester_id=req.requester_id,
        reason=req.reason,
        status=req.status.value if hasattr(req.status, "value") else req.status,
        reviewed_by=req.reviewed_by,
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
    },
)
async def list_approved_bots(
    server_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get all approved bots for a server."""
    try:
        bots = applications.get_approved_bots(server_id=server_id, status="approved")
        return [_approved_bot_to_response(bot) for bot in bots]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    server_id: int,
    body: RequestBotRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Request approval for a bot on a server."""
    try:
        req = applications.request_bot(
            server_id=server_id,
            application_id=body.application_id,
            requester_id=current_user.user_id,
            reason=body.reason,
        )
        return _bot_request_to_response(req)
    except BotRequestExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except BotAlreadyApprovedError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    server_id: int,
    application_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Remove an approved bot from a server."""
    try:
        applications.remove_approved_bot(
            server_id=server_id,
            application_id=application_id,
            user_id=current_user.user_id,
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": str(e)}},
        )
    except InstallationNotFoundError:
        pass
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    bot_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Disable a bot owned by the current user."""
    from src.core import auth

    try:
        auth.disable_bot(owner_id=current_user.user_id, bot_id=bot_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": str(e)}},
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
    bot_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Enable a bot owned by the current user."""
    from src.core import auth

    try:
        auth.enable_bot(owner_id=current_user.user_id, bot_id=bot_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": str(e)}},
        )


# === Bot Request Endpoints ===


@router.get(
    "/servers/{server_id}/requests",
    response_model=List[BotRequestResponse],
    summary="List bot requests for a server",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_bot_requests(
    server_id: int,
    status_filter: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get bot requests for a server."""
    try:
        requests = applications.get_bot_requests(
            server_id=server_id,
            status=status_filter,
        )
        return [_bot_request_to_response(req) for req in requests]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    server_id: int,
    body: ApproveBotRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Approve a bot for installation on a server."""
    try:
        bot = applications.approve_bot(
            server_id=server_id,
            application_id=body.application_id,
            approved_by=current_user.user_id,
            permissions=body.permissions,
            bot_name=body.bot_name,
        )
        return _approved_bot_to_response(bot)
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": str(e)}},
        )
    except BotAlreadyApprovedError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except BotLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": 400,
                    "message": str(e),
                    "max_allowed": e.max_allowed,
                    "current": e.current,
                }
            },
        )
    except LicenseFeatureError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"error": {"code": 402, "message": str(e), "feature": e.feature}},
        )
    except ApplicationNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": str(e)}},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    server_id: int,
    request_id: int,
    body: ReviewBotRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Review a bot request (approve or deny)."""
    try:
        req = applications.review_bot_request(
            server_id=server_id,
            request_id=request_id,
            reviewer_id=current_user.user_id,
            approve=body.approve,
            review_reason=body.review_reason,
        )
        return _bot_request_to_response(req)
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": str(e)}},
        )
    except BotRequestError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": str(e)}},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )


# === Bot Profile Endpoints ===


@router.get(
    "/profiles/{application_id}",
    response_model=BotProfileResponse,
    summary="Get a bot's public profile",
    responses={
        404: {"model": ErrorResponse, "description": "Profile not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_bot_profile(application_id: int):
    """Get a bot's public profile."""
    try:
        profile = applications.get_bot_profile(application_id)
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Bot profile not found"}},
            )
        return BotProfileResponse(
            application_id=profile.application_id,
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    application_id: int,
    body: UpdateBotProfileRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Update a bot's public profile."""
    try:
        profile = applications.update_bot_profile(
            application_id=application_id,
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
            application_id=profile.application_id,
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
    except ApplicationNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": str(e)}},
        )
    except ApplicationAccessDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": str(e)}},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )


# === Bot Directory Endpoints ===


@router.get(
    "/directory",
    response_model=BotDirectoryResponse,
    summary="Browse the bot directory",
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_bot_directory(
    server_id: Optional[int] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
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
        entries = [BotDirectoryEntry(**bot) for bot in result["bot_list"]]
        return BotDirectoryResponse(
            bots=entries,
            total=result["total"],
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    try:
        apps = applications.get_user_authorized_apps(current_user.user_id)
        return [
            AuthorizedAppResponse(
                id=app.id,
                application_id=app.application_id,
                application_name=app.application_name,
                application_icon=app.application_icon,
                scopes=app.scopes,
                authorized_at=app.authorized_at,
                last_used_at=app.last_used_at,
            )
            for app in apps
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.delete(
    "/authorized-apps/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an authorized application",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_authorized_app(
    token_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Revoke an authorized application's access."""
    try:
        applications.revoke_authorized_app(
            token_id=token_id,
            user_id=current_user.user_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )
