"""
Relationship routes - Friend and block management endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.relationships import (
    FriendRequestCreate,
    BlockCreate,
    RelationshipResponse,
)

router = APIRouter()


def _relationship_to_response(rel) -> RelationshipResponse:
    """Convert relationship object to response model."""
    status = getattr(rel, "status", None)
    if status is not None and hasattr(status, "value"):
        status = status.value
    
    return RelationshipResponse(
        user_id=str(getattr(rel, "user_id", 0) or getattr(rel, "target_id", 0)),
        status=status or "none",
        created_at=getattr(rel, "created_at", None),
    )


@router.get("/@me")
async def get_relationships(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all relationships for current user.
    
    Returns friends, pending requests, and blocked users with user info.
    """
    relationships = api.get_relationships()
    auth = api.get_auth()
    presence = api.get_presence()
    
    if not relationships:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Relationships module not available"}})
    
    def get_user_info(user_id):
        """Get username and presence for a user."""
        username = None
        avatar_url = None
        presence_data = None
        
        if auth:
            try:
                user = auth.get_user(user_id)
                if user:
                    username = user.username
                    avatar_url = getattr(user, "avatar_url", None)
            except Exception:
                pass
        
        if presence:
            try:
                pres = presence.get_visible_presence(current_user.user_id, user_id)
                if pres:
                    status = getattr(pres, "status", None)
                    if status and hasattr(status, "value"):
                        status = status.value
                    presence_data = {"status": status or "offline"}
            except Exception:
                pass
        
        return username, avatar_url, presence_data
    
    try:
        friends = relationships.get_friends(current_user.user_id)
        pending_in = relationships.get_pending_requests_incoming(current_user.user_id)
        pending_out = relationships.get_pending_requests_outgoing(current_user.user_id)
        blocked = relationships.get_blocked_users(current_user.user_id)
        
        result = []
        
        for f in friends:
            # friend_id is the OTHER user in the friendship, user_id is the current user
            friend_user_id = getattr(f, "friend_id", 0) or getattr(f, "user_id", 0)
            username, avatar_url, presence_data = get_user_info(friend_user_id)
            result.append({
                "user_id": str(friend_user_id),
                "username": username or f"User {friend_user_id}",
                "avatar_url": avatar_url,
                "status": "friend",
                "presence": presence_data,
                "created_at": getattr(f, "created_at", None),
            })
        
        for r in pending_in:
            user_id = getattr(r, "sender_id", 0)
            username, avatar_url, presence_data = get_user_info(user_id)
            result.append({
                "user_id": str(user_id),
                "username": username or f"User {user_id}",
                "avatar_url": avatar_url,
                "status": "pending_incoming",
                "presence": presence_data,
                "message": getattr(r, "message", None),
                "created_at": getattr(r, "created_at", None),
            })
        
        for r in pending_out:
            user_id = getattr(r, "recipient_id", 0)
            username, avatar_url, presence_data = get_user_info(user_id)
            result.append({
                "user_id": str(user_id),
                "username": username or f"User {user_id}",
                "avatar_url": avatar_url,
                "status": "pending_outgoing",
                "presence": presence_data,
                "created_at": getattr(r, "created_at", None),
            })
        
        for b in blocked:
            user_id = getattr(b, "blocked_id", 0)
            username, avatar_url, _ = get_user_info(user_id)
            result.append({
                "user_id": str(user_id),
                "username": username or f"User {user_id}",
                "avatar_url": avatar_url,
                "status": "blocked",
                "presence": None,
                "created_at": getattr(b, "created_at", None),
            })
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("", response_model=RelationshipResponse)
async def create_relationship(
    body: FriendRequestCreate,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Send a friend request.
    
    Creates a pending friend request to the specified user.
    """
    relationships = api.get_relationships()
    if not relationships:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Relationships module not available"}})
    
    try:
        target_id = int(body.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})
    
    try:
        request = relationships.send_friend_request(
            sender_id=current_user.user_id,
            recipient_id=target_id,
            message=body.message
        )
        
        return RelationshipResponse(
            user_id=str(target_id),
            status="pending_outgoing",
            created_at=getattr(request, "created_at", None),
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Self" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Cannot send friend request to yourself"}})
        elif "Blocked" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Exists" in exc_name or "Already" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": str(e)}})
        elif "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        raise


@router.put("/{user_id}/accept")
async def accept_friend_request(user_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Accept a friend request.
    
    Accepts a pending friend request from the specified user.
    """
    relationships = api.get_relationships()
    if not relationships:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Relationships module not available"}})
    
    try:
        sender_id = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})
    
    try:
        pending = relationships.get_pending_requests_incoming(current_user.user_id)
        request_id = None
        for r in pending:
            if getattr(r, "sender_id", 0) == sender_id:
                request_id = r.id
                break
        
        if not request_id:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Friend request not found"}})
        
        relationships.accept_friend_request(current_user.user_id, request_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Friend request not found"}})
        raise


@router.delete("/{user_id}")
async def delete_relationship(user_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Remove a relationship.
    
    Removes friend, declines request, or unblocks user.
    """
    relationships = api.get_relationships()
    if not relationships:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Relationships module not available"}})
    
    try:
        target_id = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})
    
    try:
        rel = relationships.get_relationship(current_user.user_id, target_id)
        status = getattr(rel, "status", None)
        if status is not None and hasattr(status, "value"):
            status = status.value
        
        if status == "friend":
            relationships.remove_friend(current_user.user_id, target_id)
        elif status == "blocked":
            relationships.unblock_user(current_user.user_id, target_id)
        elif status == "pending_incoming":
            pending = relationships.get_pending_requests_incoming(current_user.user_id)
            for r in pending:
                if getattr(r, "sender_id", 0) == target_id:
                    relationships.decline_friend_request(current_user.user_id, r.id)
                    break
        elif status == "pending_outgoing":
            pending = relationships.get_pending_requests_outgoing(current_user.user_id)
            for r in pending:
                if getattr(r, "recipient_id", 0) == target_id:
                    relationships.cancel_friend_request(current_user.user_id, r.id)
                    break
        
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Relationship not found"}})
        raise


@router.post("/block", response_model=RelationshipResponse)
async def block_user(body: BlockCreate, current_user: TokenInfo = Depends(get_current_user)):
    """
    Block a user.
    
    Blocks the specified user, removing any existing relationship.
    """
    relationships = api.get_relationships()
    if not relationships:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Relationships module not available"}})
    
    try:
        target_id = int(body.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})
    
    try:
        block = relationships.block_user(current_user.user_id, target_id)
        
        return RelationshipResponse(
            user_id=str(target_id),
            status="blocked",
            created_at=getattr(block, "created_at", None),
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Self" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Cannot block yourself"}})
        elif "Already" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": str(e)}})
        elif "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        raise
