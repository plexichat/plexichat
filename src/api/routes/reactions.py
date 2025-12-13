"""
Reaction routes - Message reaction endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.reactions import ReactionResponse, ReactionUserResponse
from src.api.schemas.common import SnowflakeID

router = APIRouter()


async def _dispatch_reaction_event(event_type: str, user_id: int, message_id: int, channel_id: int, emoji: str):
    """Helper to dispatch reaction events via WebSocket."""
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if not ws_is_setup():
            return

        dispatcher = get_dispatcher()

        # Get conversation participants to dispatch to
        db = api.get_db()
        if not db:
            return

        # Get the message to find conversation_id
        msg_row = db.fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ?",
            (message_id,)
        )
        if not msg_row:
            return

        conversation_id = msg_row["conversation_id"]

        # Get all participants in the conversation
        participant_rows = db.fetch_all(
            "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
            (conversation_id,)
        )
        participant_ids = [row["user_id"] for row in participant_rows]

        # Also check if this is a server channel and get server members
        import json
        conv_row = db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?",
            (conversation_id,)
        )
        if conv_row and conv_row.get("metadata"):
            try:
                metadata = json.loads(conv_row["metadata"]) if isinstance(conv_row["metadata"], str) else conv_row["metadata"]
                server_id = metadata.get("server_id") if isinstance(metadata, dict) else None
                if server_id:
                    member_rows = db.fetch_all(
                        "SELECT user_id FROM srv_members WHERE server_id = ?",
                        (server_id,)
                    )
                    for row in member_rows:
                        if row["user_id"] not in participant_ids:
                            participant_ids.append(row["user_id"])
            except (json.JSONDecodeError, TypeError):
                pass

        if not participant_ids:
            return

        # Create and dispatch the event
        evt_type = EventType.MESSAGE_REACTION_ADD if event_type == "add" else EventType.MESSAGE_REACTION_REMOVE
        event = Event(
            event_type=evt_type,
            data={
                "user_id": str(user_id),
                "message_id": str(message_id),
                "channel_id": str(channel_id),
                "emoji": emoji,
            }
        )
        await dispatcher.dispatch_event(event, participant_ids)

    except Exception as e:
        import utils.logger as logger
        logger.debug(f"Failed to dispatch reaction event: {e}")


@router.put("/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}")
async def add_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Add a reaction to a message.
    
    Adds the specified emoji reaction to the message.
    """
    import utils.logger as logger

    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(status_code=503, detail={"error": {"code": 503, "message": "Reactions module not available"}})

    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        # URL decode the emoji if needed (handles encoded emojis like %F0%9F%A5%BA)
        import urllib.parse
        decoded_emoji = urllib.parse.unquote(emoji)
        logger.debug(f"Adding reaction: user={current_user.user_id}, message={mid}, emoji={decoded_emoji}")
        reactions.add_reaction(current_user.user_id, mid, decoded_emoji)

        # Dispatch WebSocket event
        await _dispatch_reaction_event("add", current_user.user_id, mid, cid, decoded_emoji)

        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        logger.debug(f"Reaction add exception: {exc_name}: {e}")
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Exists" in exc_name:
            return {"success": True}
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Invalid" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Limit" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Blocked" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Cannot react to this message"}})
        # Log and return a proper error instead of re-raising
        logger.error(f"Reaction add error: {exc_name}: {e}")
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": f"Failed to add reaction: {str(e)}"}})


@router.delete("/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}")
async def remove_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Remove own reaction from a message.
    
    Removes the specified emoji reaction from the message.
    """
    import utils.logger as logger

    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(status_code=503, detail={"error": {"code": 503, "message": "Reactions module not available"}})

    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        # URL decode the emoji if needed
        import urllib.parse
        decoded_emoji = urllib.parse.unquote(emoji)
        reactions.remove_reaction(current_user.user_id, mid, decoded_emoji)

        # Dispatch WebSocket event
        await _dispatch_reaction_event("remove", current_user.user_id, mid, cid, decoded_emoji)

        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            return {"success": True}
        # Log and return a proper error instead of re-raising
        logger.error(f"Reaction remove error: {e}")
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to remove reaction"}})


@router.get("/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}", response_model=List[ReactionUserResponse])
async def get_reaction_users(
    channel_id: str,
    message_id: str,
    emoji: str,
    limit: int = Query(default=50, ge=1, le=100),
    after: Optional[str] = Query(default=None),
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get users who reacted with an emoji.
    
    Returns a list of users who added the specified reaction.
    """
    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})

    after_id = int(after) if after else None

    try:
        users = reactions.get_reaction_users(
            user_id=current_user.user_id,
            message_id=mid,
            emoji=emoji,
            limit=limit,
            after_user_id=after_id
        )

        return [
            ReactionUserResponse(
                user_id=SnowflakeID(u.user_id),
                reacted_at=u.reacted_at
            )
            for u in users
        ]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        raise


@router.get("/channels/{channel_id}/messages/{message_id}/reactions", response_model=List[ReactionResponse])
async def get_reactions(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get all reactions on a message.
    
    Returns a list of all emoji reactions with counts.
    """
    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})

    try:
        result = reactions.get_reactions(current_user.user_id, mid)

        return [
            ReactionResponse(
                emoji=r.emoji,
                count=r.count,
                me=r.me
            )
            for r in result.reactions
        ]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        raise
