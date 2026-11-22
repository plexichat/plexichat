"""
Webhook routes - Webhook management and execution endpoints.
"""

from typing import Optional, Union
from fastapi import APIRouter, HTTPException, Depends, status, Header

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo

# A bot token's ``user_id`` is its human owner (the bot account is in
# ``account_id``). Every management handler below authorizes against
# ``current_user.user_id`` via the webhooks core, which checks the owner's
# server roles. Without this gate a bot holding only its narrow global token
# can create/regenerate/update/delete webhooks on any server its owner manages
# -- owner authority wrongly inherited by the bot. These are human-account
# routes; reject bot credentials up front.
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.api.schemas.webhooks import (
    WebhookCreateRequest,
    WebhookUpdateRequest,
    WebhookResponse,
    WebhookExecuteRequest,
    WebhookMessageResponse,
)
from src.core.embeds.exceptions import EmbedError
from src.core.webhooks.exceptions import InvalidWebhookTokenError
from src.core.security.hardening import HardeningViolation, parse_id
from .exception_mapping import raise_mapped

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

#: Snowflake ids are at most 19 decimal digits. Bound raw path/query/body
#: ids before integer conversion so oversized values cannot reach token
#: formatting, signature verification or storage lookups.
MAX_WEBHOOK_ID_LENGTH = 32


def _parse_webhook_id(raw, field: str = "webhook_id") -> int:
    """Parse a webhook/channel/thread id as a positive snowflake.

    Bare ``int()`` accepted negatives, zero, values past 2**63-1 and
    forms such as ``1_2``; those flowed into token formatting, signature
    verification and SQL lookups as though real identifiers. Raise
    ValueError so every caller keeps its existing 400 contract.
    """
    text = str(raw).strip()
    if len(text) > MAX_WEBHOOK_ID_LENGTH or not text.isdigit():
        raise ValueError(f"{field} is not a valid snowflake")
    try:
        return parse_id(text, field)
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc


# NOTE: the webhook write routes used to call ``invalidate_pattern("webhooks:*")``
# after every create/update/delete/regenerate. Nothing in the codebase ever
# caches under a ``webhooks:`` key (the core webhook manager is uncached), so
# that call was a pure full-keyspace scan on Valkey plus an in-memory key sweep
# on every write. State changes are still broadcast to gateway clients via
# ``emit_webhooks_update``; no cache invalidation is required.


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
            require_signed_deliveries=bool(
                getattr(webhook, "require_signed_deliveries", False)
            ),
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
    require_user_token(current_user)
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = _parse_webhook_id(webhook_id)
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
            return _webhook_to_response(webhook, include_token=True)
        except Exception as e:
            raise_mapped(
                e,
                not_found_message="Webhook not found",
                permission_message="Permission denied",
                invalid_message="Invalid request",
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
    require_user_token(current_user)
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            cid = _parse_webhook_id(body.channel_id, "channel_id")
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
                require_signed_deliveries=bool(body.require_signed_deliveries),
            )
            logger.info(
                f"User {current_user.user_id} created webhook {webhook.id} in channel {cid}"
            )
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
            raise_mapped(
                e,
                not_found_message="Channel not found",
                permission_message="Permission denied",
                invalid_message="Invalid webhook name, avatar or limit reached",
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
    require_user_token(current_user)
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = _parse_webhook_id(webhook_id)
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
    require_user_token(current_user)
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = _parse_webhook_id(webhook_id)
            # Validate an optional channel move under the same 400 contract as
            # the webhook id, before the manager call's broad exception map.
            channel_id = (
                _parse_webhook_id(body.channel_id, "channel_id")
                if body.channel_id
                else None
            )
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
                channel_id=channel_id,
                require_signed_deliveries=body.require_signed_deliveries,
            )
            logger.info(f"User {current_user.user_id} updated webhook {wid}")
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
            raise_mapped(
                e,
                not_found_message="Webhook not found",
                permission_message="Permission denied",
                invalid_message="Invalid webhook name or avatar",
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
    require_user_token(current_user)
    webhooks = api.get_webhooks()
    if not webhooks:
        logger.error("Webhooks module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        try:
            wid = _parse_webhook_id(webhook_id)
        except ValueError:
            logger.warning(
                f"User {current_user.user_id} provided invalid webhook ID for deletion: {webhook_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid webhook ID"}},
            )

        try:
            # Capture scope BEFORE deletion so gateway clients can refresh
            # their open webhook panels (emitting guild_id=0 makes them
            # ignore the event).
            try:
                existing = webhooks.get_webhook(wid)
                deleted_server_id = getattr(existing, "server_id", None) or 0
                deleted_channel_id = getattr(existing, "channel_id", None)
            except Exception:
                deleted_server_id = 0
                deleted_channel_id = None

            webhooks.delete_webhook(current_user.user_id, wid)
            logger.info(f"User {current_user.user_id} deleted webhook {wid}")
            try:
                from src.core.events.gateway_emit import emit_webhooks_update

                emit_webhooks_update(deleted_server_id, channel_id=deleted_channel_id)
            except Exception as ge:
                logger.debug(f"emit_webhooks_update failed: {ge}")
            return SuccessResponse(success=True, message=None)
        except Exception as e:
            raise_mapped(
                e,
                not_found_message="Webhook not found",
                permission_message="Permission denied",
                invalid_message="Invalid request",
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
    x_signature: Optional[str] = Header(None, alias="X-Plexichat-Signature"),
    x_timestamp: Optional[str] = Header(None, alias="X-Plexichat-Timestamp"),
) -> Union[WebhookMessageResponse, SuccessResponse]:
    """
    Execute a webhook.

    Sends a message to the webhook's channel using its token. When the
    caller also supplies an ``X-Plexichat-Signature`` /
    ``X-Plexichat-Timestamp`` pair, the payload is verified against the
    webhook's Ed25519 key before execution (rejecting tampered or
    replayed deliveries).
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
            wid = _parse_webhook_id(webhook_id)
        except ValueError:
            logger.warning(
                f"Invalid webhook ID format in execute_webhook: {webhook_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid webhook ID"}},
            )

        tid = None
        if thread_id and thread_id.strip().lower() != "null":
            try:
                tid = _parse_webhook_id(thread_id, "thread_id")
            except (ValueError, TypeError):
                logger.warning(
                    f"Invalid thread ID format in execute_webhook: {thread_id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": "Invalid thread ID"}},
                )
        if (
            tid is None
            and body.thread_id
            and str(body.thread_id).strip().lower() != "null"
        ):
            try:
                tid = _parse_webhook_id(body.thread_id, "thread_id")
            except (ValueError, TypeError):
                logger.warning(
                    f"Invalid thread ID format in execute_webhook: {body.thread_id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": "Invalid thread ID"}},
                )

        try:
            # Signature headers come as a PAIR. A caller that supplies only
            # one of them must never fall through to the unsigned path -- that
            # would silently downgrade a signed delivery to token-only auth.
            if bool(x_signature) != bool(x_timestamp):
                logger.warning(
                    f"Incomplete signature header pair on webhook {wid} execution"
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {
                            "code": 401,
                            "message": "Incomplete webhook signature headers",
                        }
                    },
                )

            has_signature = bool(x_signature and x_timestamp)
            # F8: fail CLOSED. The only webhook allowed to execute unsigned is
            # one whose persisted record explicitly says signatures are not
            # required. An unreadable record (or a DB error) must be treated
            # as requiring a signature instead of silently disabling the
            # check.
            requires_signature = True
            if not has_signature:
                try:
                    webhook_record = webhooks.get_webhook(wid)
                    requires_signature = bool(
                        getattr(webhook_record, "require_signed_deliveries", True)
                    )
                except Exception as e:
                    logger.error(
                        f"Could not read signature policy for webhook {wid}: {e}"
                    )
                    requires_signature = True

            if has_signature:
                # Canonicalize the thread id to its decimal string so the
                # signed bytes agree no matter which source won above: ``tid``
                # is an int (query param or parsed body) while
                # ``body.thread_id`` is a ``SnowflakeID`` str. Signing
                # ``123`` vs ``"123"`` yields different JSON bytes, so a
                # caller that signed the string form would fail verification
                # when the int form won here (and vice versa).
                if tid is not None:
                    thread_str: Optional[str] = str(tid)
                elif body.thread_id and str(body.thread_id).strip().lower() != "null":
                    thread_str = str(
                        _parse_webhook_id(str(body.thread_id).strip(), "thread_id")
                    )
                else:
                    thread_str = None
                payload = {
                    "content": body.content,
                    "username": body.username,
                    "avatar_url": body.avatar_url,
                    "embeds": body.embeds,
                    "thread_id": thread_str,
                }
                if not webhooks.verify_payload(wid, payload, x_signature, x_timestamp):
                    logger.warning(
                        f"Invalid Ed25519 signature on webhook {wid} execution"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={
                            "error": {
                                "code": 401,
                                "message": "Invalid webhook signature",
                            }
                        },
                    )
            elif requires_signature:
                logger.warning(
                    f"Unsigned execution refused for webhook {wid} "
                    f"(require_signed_deliveries enabled or policy unreadable)"
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": {
                            "code": 401,
                            "message": "This webhook requires signed deliveries",
                        }
                    },
                )

            message = webhooks.execute_webhook(
                webhook_id=wid,
                token=token,
                content=body.content,
                username=body.username,
                avatar_url=body.avatar_url,
                embeds=body.embeds,
                thread_id=tid,
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
        except EmbedError as e:
            logger.warning(
                f"Embed validation error during execution of webhook {wid}: {e}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid request"}},
            )
        except InvalidWebhookTokenError:
            logger.warning(f"Invalid token provided for webhook {wid} execution")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": 401, "message": "Invalid webhook token"}},
            )
        except Exception as e:
            raise_mapped(
                e,
                not_found_message="Webhook not found",
                permission_message="Permission denied",
                invalid_message="Invalid webhook execution request",
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
