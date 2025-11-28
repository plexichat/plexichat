"""
Reaction routes - Message reaction endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.reactions import ReactionResponse, ReactionUserResponse

router = APIRouter()


@router.put("/channels/{channel_id}/messages/{message_id}/reactions/{emoji}")
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
    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})
    
    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})
    
    try:
        reactions.add_reaction(current_user.user_id, mid, emoji)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
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
        raise


@router.delete("/channels/{channel_id}/messages/{message_id}/reactions/{emoji}")
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
    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})
    
    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})
    
    try:
        reactions.remove_reaction(current_user.user_id, mid, emoji)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            return {"success": True}
        raise


@router.get("/channels/{channel_id}/messages/{message_id}/reactions/{emoji}", response_model=List[ReactionUserResponse])
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
                user_id=str(u.user_id),
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
