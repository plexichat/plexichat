"""
Reaction routes - Message reaction endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.reactions import ReactionResponse, ReactionUserResponse
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
import utils.logger as logger

router = APIRouter(tags=["Reactions"])


async def _dispatch_reaction_event(
    event_type: str, user_id: int, message_id: int, channel_id: int, emoji: str
):
    """Helper to dispatch reaction events via WebSocket."""
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if not ws_is_setup():
            return

        dispatcher = get_dispatcher()
        reactions = api.get_reactions()

        if not reactions:
            return

        # Get the conversation ID from the message
        conversation_id = reactions.get_conversation_id_from_message(message_id)
        if not conversation_id:
            return

        # Get all participants in the conversation (including server members)
        participant_ids = reactions.get_participant_ids(conversation_id)

        if not participant_ids:
            return

        # Create and dispatch the event
        evt_type = (
            EventType.MESSAGE_REACTION_ADD
            if event_type == "add"
            else EventType.MESSAGE_REACTION_REMOVE
        )
        event = Event(
            event_type=evt_type,
            data={
                "user_id": str(user_id),
                "message_id": str(message_id),
                "channel_id": str(channel_id),
                "emoji": emoji,
            },
        )
        await dispatcher.dispatch_event(event, participant_ids)

    except Exception as e:
        logger.debug(f"Failed to dispatch reaction event for message {message_id}: {e}")


@router.put(
    "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}",
    response_model=SuccessResponse,
    summary="Add reaction",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid message ID or channel ID or emoji",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Reactions module not available"},
    },
)
async def add_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Add a reaction to a message.

    Adds the specified emoji reaction to the message.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid message ID format for reaction: {message_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format for reaction: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            # URL decode the emoji if needed (handles encoded emojis like %F0%9F%A5%BA)
            import urllib.parse

            decoded_emoji = urllib.parse.unquote(emoji)

            reactions.add_reaction(current_user.user_id, mid, decoded_emoji)

            # Dispatch WebSocket event
            await _dispatch_reaction_event(
                "add", current_user.user_id, mid, cid, decoded_emoji
            )

            return SuccessResponse(success=True)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )
            elif "Exists" in exc_name:
                return SuccessResponse(success=True)
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to react to message {mid}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )
            elif "Invalid" in exc_name or "Limit" in exc_name:
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )
            elif "Blocked" in exc_name:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot react to this message",
                        }
                    },
                )

            logger.error(
                f"Failed to add reaction to message {mid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to add reaction: {str(e)}",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in add_reaction for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}",
    response_model=SuccessResponse,
    summary="Remove reaction",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid message ID or channel ID or emoji",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Reactions module not available"},
    },
)
async def remove_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Remove own reaction from a message.

    Removes the specified emoji reaction from the message.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid message ID format for reaction removal: {message_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        try:
            cid = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid channel ID format for reaction removal: {channel_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            # URL decode the emoji if needed
            import urllib.parse

            decoded_emoji = urllib.parse.unquote(emoji)

            reactions.remove_reaction(current_user.user_id, mid, decoded_emoji)

            # Dispatch WebSocket event
            await _dispatch_reaction_event(
                "remove", current_user.user_id, mid, cid, decoded_emoji
            )

            return SuccessResponse(success=True)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                return SuccessResponse(success=True)

            logger.error(
                f"Failed to remove reaction from message {mid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to remove reaction"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in remove_reaction for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}",
    response_model=List[ReactionUserResponse],
    summary="Get reaction users",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid message ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_reaction_users(
    channel_id: str,
    message_id: str,
    emoji: str,
    limit: int = Query(default=50, ge=1, le=100),
    after: Optional[str] = Query(default=None),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[ReactionUserResponse]:
    """
    Get users who reacted with an emoji.

    Returns a list of users who added the specified reaction.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid message ID format for reaction users: {message_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        try:
            after_id = int(after) if after else None
        except (ValueError, TypeError):
            logger.warning(f"Invalid 'after' ID format for reaction users: {after}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid after ID"}},
            )

        try:
            users = reactions.get_reaction_users(
                user_id=current_user.user_id,
                message_id=mid,
                emoji=emoji,
                limit=limit,
                after_user_id=after_id,
            )

            return [
                ReactionUserResponse(
                    user_id=SnowflakeID(u.user_id), reacted_at=u.reacted_at
                )
                for u in users
            ]
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )

            logger.error(
                f"Failed to fetch reaction users for message {mid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to fetch reaction users: {str(e)}",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_reaction_users for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/channels/{channel_id}/messages/{message_id}/reactions",
    response_model=List[ReactionResponse],
    summary="Get reactions",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid message ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_reactions(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[ReactionResponse]:
    """
    Get all reactions on a message.

    Returns a list of all emoji reactions with counts.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid message ID format for reactions: {message_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        try:
            result = reactions.get_reactions(current_user.user_id, mid)

            return [
                ReactionResponse(emoji=r.emoji, count=r.count, me=r.me)
                for r in result.reactions
            ]
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )

            logger.error(
                f"Failed to fetch reactions for message {mid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to fetch reactions: {str(e)}",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_reactions for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
