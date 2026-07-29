"""
Webhook routes - Webhook management and execution endpoints.
"""

from typing import Optional, Union
from fastapi import APIRouter, HTTPException, Depends, status

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.api.schemas.webhooks import (
    WebhookCreateRequest,
    WebhookUpdateRequest,
    WebhookResponse,
    WebhookExecuteRequest,
    WebhookMessageResponse,
)
from src.core.database.cache import invalidate_pattern

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _webhook_to_response(webhook, include_token: bool = False) -> WebhookResponse:
    """
    Convert a core webhook object to an API response model.

    Optionally includes the secure token and full URL.
    """
    try:
        return WebhookResponse(
            id=SnowflakeID(webhook.id),
            channel_id=SnowflakeID(webhook.channel_id),
            server_id=SnowflakeID(webhook.server_id),
            creator_id=SnowflakeID(getattr(webhook, "creator_id", 0)),
            name=webhook.name,
            avatar_url=webhook.avatar_url,
            token=webhook.token if include_token and webhook.token else None,
            url=webhook.url if include_token and webhook.token else None,
            created_at=webhook.created_at,
        )
    except Exception as e:
        logger.error(
            f"Failed to convert webhook {getattr(webhook, 'id', 'unknown')} to response: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/{webhook_id}/regenerate-token",
    response_model=WebhookResponse,
    summary="Regenerate webhook token",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid webhook ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Webhook not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def regenerate_webhook_token(
    webhook_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> WebhookResponse:
    """
    Generate a new secure token for a webhook.

    Invalidates the old token and returns the updated webhook with the new token.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = int(webhook_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid webhook ID for token regeneration: {webhook_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid webhook ID"}},
            )

        try:
            webhook = webhooks.regenerate_token(current_user.user_id, wid)
            logger.info(
                f"User {current_user.user_id} regenerated token for webhook {wid}"
            )
            # Invalidate webhook cache for this server
            invalidate_pattern("webhooks:*")
            return _webhook_to_response(webhook, include_token=True)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Webhook not found"}},
                )
            if "Permission" in exc_name:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": str(e)}},
                )
            logger.error(
                f"Unexpected error regenerating token for webhook {wid} by user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected top-level error in regenerate_webhook_token for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "",
    response_model=WebhookResponse,
    summary="Create a webhook",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid ID format or name/avatar too long",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_webhook(
    body: WebhookCreateRequest, current_user: TokenInfo = Depends(get_current_user)
) -> WebhookResponse:
    """
    Create a new webhook.

    Creates a webhook for the specified channel. Returns the token only on creation.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            cid = int(body.channel_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid channel ID for webhook creation: {body.channel_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            webhook = webhooks.create_webhook(
                user_id=current_user.user_id,
                channel_id=cid,
                name=body.name,
                avatar_url=body.avatar_url,
            )
            logger.info(
                f"User {current_user.user_id} created webhook {webhook.id} in channel {cid}"
            )
            # Invalidate webhook cache for this server
            invalidate_pattern("webhooks:*")
            try:
                from src.core.events.gateway_emit import emit_webhooks_update

                emit_webhooks_update(
                    getattr(webhook, "server_id", None) or 0,
                    channel_id=getattr(webhook, "channel_id", None) or cid,
                )
            except Exception as ge:
                logger.debug(f"emit_webhooks_update failed: {ge}")
            return _webhook_to_response(webhook, include_token=True)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                logger.warning(
                    f"Channel {cid} not found for webhook creation by user {current_user.user_id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"Permission denied for user {current_user.user_id} creating webhook in channel {cid}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": str(e)}},
                )
            elif "Limit" in exc_name:
                logger.warning(
                    f"Webhook limit reached for channel {cid} (user: {current_user.user_id})"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": str(e)}},
                )
            elif "Name" in exc_name or "Avatar" in exc_name:
                logger.warning(
                    f"Invalid name/avatar for webhook creation in channel {cid} (user: {current_user.user_id}): {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": str(e)}},
                )

            logger.error(
                f"Unexpected error in create_webhook for channel {cid} by user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected top-level error in create_webhook for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{webhook_id}",
    response_model=WebhookResponse,
    summary="Get a webhook",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid webhook ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Webhook not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_webhook(
    webhook_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> WebhookResponse:
    """
    Get webhook by ID.

    Returns webhook information without the token.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = int(webhook_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid webhook ID: {webhook_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid webhook ID"}},
            )

        try:
            webhook = webhooks.get_webhook(wid, current_user.user_id)
            if not webhook:
                logger.warning(
                    f"Webhook {wid} not found for user {current_user.user_id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Webhook not found"}},
                )
            return _webhook_to_response(webhook)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error fetching webhook {wid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected top-level error in get_webhook for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{webhook_id}",
    response_model=WebhookResponse,
    summary="Update a webhook",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid webhook ID or name too long",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Webhook or channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_webhook(
    webhook_id: str,
    body: WebhookUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> WebhookResponse:
    """
    Update a webhook.

    Allows changing name, avatar, or channel.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = int(webhook_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid webhook ID for update: {webhook_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid webhook ID"}},
            )

        try:
            webhook = webhooks.update_webhook(
                user_id=current_user.user_id,
                webhook_id=wid,
                name=body.name,
                avatar_url=body.avatar_url,
                channel_id=int(body.channel_id) if body.channel_id else None,
            )
            logger.info(f"User {current_user.user_id} updated webhook {wid}")
            # Invalidate webhook cache for this server
            invalidate_pattern("webhooks:*")
            try:
                from src.core.events.gateway_emit import emit_webhooks_update

                emit_webhooks_update(
                    getattr(webhook, "server_id", None) or 0,
                    channel_id=getattr(webhook, "channel_id", None),
                )
            except Exception as ge:
                logger.debug(f"emit_webhooks_update failed: {ge}")
            return _webhook_to_response(webhook)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                logger.warning(
                    f"Webhook or channel not found during update of {wid} by user {current_user.user_id}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": str(e)}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"Permission denied for user {current_user.user_id} updating webhook {wid}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": str(e)}},
                )
            elif "Name" in exc_name or "Avatar" in exc_name:
                logger.warning(
                    f"Invalid name/avatar for webhook update {wid} by user {current_user.user_id}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": str(e)}},
                )

            logger.error(
                f"Unexpected error in update_webhook for {wid} by user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected top-level error in update_webhook for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{webhook_id}",
    response_model=SuccessResponse,
    summary="Delete a webhook",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid webhook ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Webhook not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_webhook(
    webhook_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Delete a webhook.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = int(webhook_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid webhook ID for deletion: {webhook_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid webhook ID"}},
            )

        try:
            webhooks.delete_webhook(current_user.user_id, wid)
            logger.info(f"User {current_user.user_id} deleted webhook {wid}")
            # Invalidate webhook cache for this server
            invalidate_pattern("webhooks:*")
            try:
                from src.core.events.gateway_emit import emit_webhooks_update

                emit_webhooks_update(0, channel_id=None)
            except Exception as ge:
                logger.debug(f"emit_webhooks_update failed: {ge}")
            return SuccessResponse(success=True, message=None)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                logger.warning(
                    f"Webhook {wid} not found for deletion by user {current_user.user_id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Webhook not found"}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"Permission denied for user {current_user.user_id} deleting webhook {wid}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": str(e)}},
                )

            logger.error(
                f"Unexpected error in delete_webhook for {wid} by user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected top-level error in delete_webhook for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/{webhook_id}/{token}",
    response_model=Union[WebhookMessageResponse, SuccessResponse],
    summary="Execute a webhook",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid webhook ID or request body",
        },
        401: {"model": ErrorResponse, "description": "Invalid token"},
        404: {"model": ErrorResponse, "description": "Webhook not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def execute_webhook(
    webhook_id: str,
    token: str,
    body: WebhookExecuteRequest,
    thread_id: Optional[str] = None,
    wait: bool = False,
) -> Union[WebhookMessageResponse, SuccessResponse]:
    """
    Execute a webhook.

    Sends a message to the webhook's channel using its token.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = int(webhook_id)
            tid = int(thread_id) if thread_id else None
        except ValueError:
            logger.warning(
                f"Invalid ID format in execute_webhook: webhook_id={webhook_id}, thread_id={thread_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid ID format"}},
            )

        try:
            message = webhooks.execute_webhook(
                webhook_id=wid,
                token=token,
                content=body.content,
                username=body.username,
                avatar_url=body.avatar_url,
                embeds=body.embeds,
                thread_id=tid or (int(body.thread_id) if body.thread_id else None),
                wait=wait,
            )

            logger.debug(f"Executed webhook {wid}")
            if not wait:
                return SuccessResponse(success=True, message=None)

            if message is None:
                logger.error(
                    f"Webhook {wid} execution returned no message with wait=true"
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"error": {"code": 500, "message": "Internal server error"}},
                )

            return WebhookMessageResponse(
                id=SnowflakeID(message.id),
                webhook_id=SnowflakeID(wid),
                channel_id=SnowflakeID(message.channel_id),
                content=message.content,
                username=getattr(message, "username", None),
                avatar_url=getattr(message, "avatar_url", None),
                created_at=message.created_at,
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                logger.warning(f"Webhook {wid} not found for execution")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Webhook not found"}},
                )
            elif "InvalidWebhookToken" in exc_name or "InvalidToken" in exc_name:
                logger.warning(f"Invalid token provided for webhook {wid} execution")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error": {"code": 401, "message": "Invalid webhook token"}},
                )
            elif (
                "Validation" in exc_name
                or "Empty" in exc_name
                or "Content" in exc_name
                or "Embed" in exc_name
            ):
                logger.warning(
                    f"Validation error during execution of webhook {wid}: {e}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": str(e)}},
                )

            logger.error(
                f"Unexpected error in execute_webhook for {wid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected top-level error in execute_webhook for {webhook_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
