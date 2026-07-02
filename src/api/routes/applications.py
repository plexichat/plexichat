"""
Application management API routes.

Handles user application CRUD, bot token management, and interaction responses.
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, status

from pydantic import BaseModel

from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core import applications
from src.core.applications import InteractionResponse, InteractionResponseType
from src.core.applications.exceptions import (
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    InteractionNotFoundError,
    InteractionExpiredError,
    InteractionAlreadyRespondedError,
    InteractionValidationError,
)


router = APIRouter(prefix="/applications", tags=["Applications"])


# === Schemas ===


class ApplicationResponse(BaseModel):
    """Response schema for an application."""

    id: int
    owner_id: int
    name: str
    description: Optional[str] = None
    icon_emoji: Optional[str] = None
    icon_url: Optional[str] = None
    bot_id: Optional[int] = None
    bot: bool = False
    bot_public: bool = True
    capabilities: Optional[Dict[str, bool]] = None
    created_at: int


class CreateApplicationRequest(BaseModel):
    """Request to create an application."""

    name: str
    description: Optional[str] = None
    icon_emoji: Optional[str] = None
    redirect_uris: Optional[List[str]] = None
    bot_public: bool = True
    bot_require_code_grant: bool = False


class UpdateApplicationRequest(BaseModel):
    """Request to update an application."""

    name: Optional[str] = None
    description: Optional[str] = None
    icon_emoji: Optional[str] = None
    redirect_uris: Optional[List[str]] = None
    bot_public: Optional[bool] = None
    bot_require_code_grant: Optional[bool] = None


class BotTokenResponse(BaseModel):
    """Response schema for bot token."""

    token: str
    bot_id: Optional[int] = None


class InteractionCallbackRequest(BaseModel):
    """Request to respond to an interaction."""

    type: int
    data: Optional[Dict[str, Any]] = None


# === Helper ===


def _application_to_response(app) -> ApplicationResponse:
    """Convert an Application model to response schema."""
    # Application model fields depend on the source
    # Support both dataclass and dict-like objects for safety
    if isinstance(app, dict):
        return ApplicationResponse(
            id=app.get("id", 0),
            owner_id=app.get("owner_id", 0),
            name=app.get("name", ""),
            description=app.get("description"),
            icon_emoji=None,
            icon_url=app.get("icon_url"),
            bot_id=app.get("bot_id"),
            bot=app.get("bot_id") is not None,
            bot_public=app.get("bot_public", True),
            capabilities=None,
            created_at=app.get("created_at", 0),
        )
    return ApplicationResponse(
        id=getattr(app, "id", 0),
        owner_id=getattr(app, "owner_id", 0),
        name=getattr(app, "name", ""),
        description=getattr(app, "description", None),
        icon_emoji=None,
        icon_url=getattr(app, "icon_url", None),
        bot_id=getattr(app, "bot_id", None),
        bot=getattr(app, "bot_id", None) is not None,
        bot_public=getattr(app, "bot_public", True),
        capabilities=None,
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
):
    """Get all applications owned by the current user."""
    try:
        apps = applications.get_user_applications(current_user.user_id)
        return [_application_to_response(app) for app in apps]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
            bot_public=body.bot_public,
            bot_require_code_grant=body.bot_require_code_grant,
        )
        return _application_to_response(app)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    application_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get details of a specific application."""
    try:
        app = applications.get_application(
            application_id=application_id,
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    application_id: int,
    body: UpdateApplicationRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Update an application's details."""
    try:
        app = applications.update_application(
            user_id=current_user.user_id,
            application_id=application_id,
            name=body.name,
            description=body.description,
            redirect_uris=body.redirect_uris,
            bot_public=body.bot_public,
            bot_require_code_grant=body.bot_require_code_grant,
        )
        return _application_to_response(app)
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
    application_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Delete an application."""
    try:
        applications.delete_application(
            user_id=current_user.user_id,
            application_id=application_id,
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
    application_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Create a bot account for an application and return its token."""
    try:
        # If bot already exists, this will raise ApplicationAccessDeniedError
        # In that case, return success with a placeholder message
        result = applications.create_bot_for_application(
            user_id=current_user.user_id,
            application_id=application_id,
        )
        return BotTokenResponse(
            token=result.get("token", ""),
            bot_id=result.get("bot_id"),
        )
    except ApplicationNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": str(e)}},
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
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
    application_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Regenerate the client secret for an application."""
    try:
        result = applications.regenerate_client_secret(
            application_id=application_id,
            user_id=current_user.user_id,
        )
        return {"secret": result}
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
        )
    except InteractionNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": 404, "message": str(e)}},
        )
    except InteractionExpiredError as e:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": {"code": 410, "message": str(e)}},
        )
    except InteractionAlreadyRespondedError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": 409, "message": str(e)}},
        )
    except InteractionValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )
