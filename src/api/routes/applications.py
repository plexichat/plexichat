"""
Application management API routes.

Handles user application CRUD, bot token management, and interaction responses.
"""

import hashlib
import logging
from typing import Annotated, Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query, status

from pydantic import BaseModel, Field, field_validator

from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SnowflakeID
from src.api.schemas.policy_types import UIText
from src.core import applications
from src.core.database import check_rate_limit
from src.core.applications import InteractionResponse, InteractionResponseType
from src.core.applications.exceptions import (
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    InteractionNotFoundError,
    InteractionExpiredError,
    InteractionAlreadyRespondedError,
    InteractionValidationError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["Applications"])

# Listing is a bounded page, not "every row this user has ever created".
MAX_APPLICATION_PAGE_SIZE = 100
MAX_APPLICATION_OFFSET = 100_000
MAX_APPLICATION_FILTER_LENGTH = 128


# === Schemas ===


class ApplicationResponse(BaseModel):
    """Response schema for an application."""

    id: SnowflakeID
    owner_id: SnowflakeID
    name: str
    description: Optional[str] = None
    icon_emoji: Optional[str] = None
    icon_url: Optional[str] = None
    bot_id: Optional[str] = None
    bot: bool = False
    bot_public: bool = True
    capabilities: Optional[Dict[str, bool]] = None
    allowed_scopes: List[str] = Field(default_factory=list)
    created_at: int


def _validate_redirect_uris(value):
    """Redirect URIs are the OAuth callback allow-list; reject unsafe shapes.

    The rules now live in the applications core so the HTTP schema, the
    manager and the OAuth flow enforce exactly the same thing.
    ``HardeningViolation`` is a ``ValueError`` subclass, so pydantic still
    reports a validation error.
    """
    if value is None:
        return value
    from src.core.applications.validation import validate_redirect_uris

    return validate_redirect_uris(value)


def _validate_description(value):
    """Descriptions share the core ceiling and reject embedded controls."""
    if value is None:
        return value
    from src.core.applications.validation import validate_application_description

    return validate_application_description(value)


def _validate_allowed_scopes(value):
    """Scope ceilings share the core allow-list with the OAuth flow."""
    if value is None:
        return value
    from src.core.applications.oauth.scopes import require_allowed_scopes

    return require_allowed_scopes(value)


class CreateApplicationRequest(BaseModel):
    """Request to create an application."""

    name: UIText
    description: Optional[str] = Field(None, max_length=2000)
    icon_emoji: Optional[str] = Field(None, max_length=16)
    redirect_uris: Optional[List[str]] = Field(None, max_length=20)
    allowed_scopes: Optional[List[str]] = Field(None, max_length=20)
    bot_public: bool = True
    bot_require_code_grant: bool = False

    @field_validator("redirect_uris")
    @classmethod
    def _redirect_uris(cls, value):
        return _validate_redirect_uris(value)

    @field_validator("allowed_scopes")
    @classmethod
    def _allowed_scopes(cls, value):
        return _validate_allowed_scopes(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _validate_description(value)


class UpdateApplicationRequest(BaseModel):
    """Request to update an application."""

    name: Optional[UIText] = None
    description: Optional[str] = Field(None, max_length=2000)
    icon_emoji: Optional[str] = Field(None, max_length=16)
    redirect_uris: Optional[List[str]] = Field(None, max_length=20)
    allowed_scopes: Optional[List[str]] = Field(None, max_length=20)
    bot_public: Optional[bool] = None
    bot_require_code_grant: Optional[bool] = None

    @field_validator("redirect_uris")
    @classmethod
    def _redirect_uris(cls, value):
        return _validate_redirect_uris(value)

    @field_validator("allowed_scopes")
    @classmethod
    def _allowed_scopes(cls, value):
        return _validate_allowed_scopes(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value):
        return _validate_description(value)


class BotTokenResponse(BaseModel):
    """Response schema for bot token."""

    token: str
    bot_id: Optional[str] = None


class InteractionCallbackRequest(BaseModel):
    """Request to respond to an interaction."""

    application_id: SnowflakeID
    type: InteractionResponseType
    data: Optional[Dict[str, Any]] = None


_INTERACTION_CALLBACK_LIMIT = 30
_INTERACTION_CALLBACK_WINDOW_SECONDS = 60


def _interaction_rate_limit_key(interaction_token: str) -> str:
    """Build a non-reversible rate-limit key without storing the bearer token."""
    token_fingerprint = hashlib.sha256(interaction_token.encode("utf-8")).hexdigest()
    return f"applications:interaction-callback:{token_fingerprint}"


# === Helper ===


def _application_to_response(app) -> ApplicationResponse:
    """Convert an Application model to response schema."""
    # Application model fields depend on the source
    # Support both dataclass and dict-like objects for safety
    if isinstance(app, dict):
        raw_bot_id = app.get("bot_id")
        return ApplicationResponse(
            id=SnowflakeID(app.get("id", 0)),
            owner_id=SnowflakeID(app.get("owner_id", 0)),
            name=app.get("name", ""),
            description=app.get("description"),
            icon_emoji=None,
            icon_url=app.get("icon_url"),
            bot_id=str(raw_bot_id) if raw_bot_id is not None else None,
            bot=raw_bot_id is not None,
            bot_public=app.get("bot_public", True),
            capabilities=None,
            allowed_scopes=list(app.get("allowed_scopes") or []),
            created_at=app.get("created_at", 0),
        )
    raw_bot_id = getattr(app, "bot_id", None)
    return ApplicationResponse(
        id=SnowflakeID(getattr(app, "id", 0)),
        owner_id=SnowflakeID(getattr(app, "owner_id", 0)),
        name=getattr(app, "name", ""),
        description=getattr(app, "description", None),
        icon_emoji=None,
        icon_url=getattr(app, "icon_url", None),
        bot_id=str(raw_bot_id) if raw_bot_id is not None else None,
        bot=raw_bot_id is not None,
        bot_public=getattr(app, "bot_public", True),
        capabilities=None,
        allowed_scopes=list(getattr(app, "allowed_scopes", None) or []),
        created_at=getattr(app, "created_at", 0),
    )


# === Endpoints ===


@router.get(
    "",
    response_model=List[ApplicationResponse],
    summary="List user's applications",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_applications(
    current_user: TokenInfo = Depends(get_current_user),
    limit: Annotated[int, Query(ge=1, le=MAX_APPLICATION_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_APPLICATION_OFFSET)] = 0,
    name_filter: Annotated[
        Optional[str], Query(max_length=MAX_APPLICATION_FILTER_LENGTH)
    ] = None,
):
    """Get a bounded page of applications owned by the current user."""
    try:
        apps = applications.get_user_applications(
            current_user.user_id,
            limit=limit,
            offset=offset,
            name_filter=name_filter,
            # ApplicationResponse never exposes redirect URIs; skip decoding
            # that JSON for every row in the page.
            parse_redirect_uris=False,
        )
        return [_application_to_response(app) for app in apps]
    except Exception:
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new application",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_application(
    body: CreateApplicationRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Create a new application for the current user."""
    try:
        app = applications.create_application(
            owner_id=current_user.user_id,
            name=body.name,
            description=body.description,
            redirect_uris=body.redirect_uris,
            allowed_scopes=body.allowed_scopes,
            bot_public=body.bot_public,
            bot_require_code_grant=body.bot_require_code_grant,
        )
        return _application_to_response(app)
    except Exception:
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="Get application details",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Application not found"},
    },
)
async def get_application(
    application_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get details of a specific application."""
    try:
        app = applications.get_application(
            application_id=int(application_id),
            user_id=current_user.user_id,
        )
        if not app:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Application not found"}},
            )
        return _application_to_response(app)
    except HTTPException:
        raise
    except ApplicationAccessDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": 403,
                    "message": "You do not own this application",
                }
            },
        )
    except Exception:
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.put(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="Update an application",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Application not found"},
    },
)
async def update_application(
    application_id: str,
    body: UpdateApplicationRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Update an application's details."""
    try:
        app = applications.update_application(
            user_id=current_user.user_id,
            application_id=int(application_id),
            name=body.name,
            description=body.description,
            redirect_uris=body.redirect_uris,
            allowed_scopes=body.allowed_scopes,
            bot_public=body.bot_public,
            bot_require_code_grant=body.bot_require_code_grant,
        )
        return _application_to_response(app)
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
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an application",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Application not found"},
    },
)
async def delete_application(
    application_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Delete an application."""
    try:
        applications.delete_application(
            user_id=current_user.user_id,
            application_id=int(application_id),
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
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/{application_id}/bot",
    response_model=BotTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a bot for an application",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Application not found"},
    },
)
async def create_bot(
    application_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Create a bot account for an application and return its token."""
    try:
        # If bot already exists, this will raise ApplicationAccessDeniedError
        # In that case, return success with a placeholder message
        result = applications.create_bot_for_application(
            user_id=current_user.user_id,
            application_id=int(application_id),
        )
        raw_bot_id = result.get("bot_id")
        return BotTokenResponse(
            token=result.get("token", ""),
            bot_id=str(raw_bot_id) if raw_bot_id is not None else None,
        )
    except ApplicationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Resource not found"}},
        )
    except ApplicationAccessDeniedError:
        # Bot already exists — indicate this so frontend can show proper message
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": 400,
                    "message": "A bot already exists for this application. Use the settings page to manage it.",
                }
            },
        )
    except Exception:
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/{application_id}/regenerate-secret",
    response_model=dict,
    summary="Regenerate client secret",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Application not found"},
    },
)
async def regenerate_secret(
    application_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Regenerate the client secret for an application."""
    try:
        result = applications.regenerate_client_secret(
            application_id=int(application_id),
            user_id=current_user.user_id,
        )
        return {"secret": result}
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
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/interactions/{interaction_token}/callback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Respond to an interaction",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid response"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def respond_to_interaction(
    interaction_token: str,
    body: InteractionCallbackRequest,
):
    """Respond to an interaction with a callback."""
    try:
        allowed, _remaining = check_rate_limit(
            _interaction_rate_limit_key(interaction_token),
            _INTERACTION_CALLBACK_LIMIT,
            _INTERACTION_CALLBACK_WINDOW_SECONDS,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {"code": 429, "message": "Too many interaction callbacks"}
                },
                headers={"Retry-After": str(_INTERACTION_CALLBACK_WINDOW_SECONDS)},
            )

        response_kwargs = {
            key: value
            for key, value in (body.data or {}).items()
            if key
            in {
                "content",
                "embeds",
                "components",
                "flags",
                "tts",
                "allowed_mentions",
                "attachments",
                "choices",
                "custom_id",
                "title",
            }
        }
        response = InteractionResponse(
            response_type=InteractionResponseType(body.type),
            **response_kwargs,
        )
        applications.create_interaction_response(
            interaction_token=interaction_token,
            response=response,
            application_id=int(body.application_id),
        )
    except HTTPException:
        raise
    except InteractionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": "Resource not found"}},
        )
    except InteractionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": {"code": 410, "message": "Resource is archived"}},
        )
    except InteractionAlreadyRespondedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": 409, "message": "Resource already exists"}},
        )
    except InteractionValidationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception:
        logger.exception("Unhandled error in applications API")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
