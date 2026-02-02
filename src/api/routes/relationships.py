"""
Relationship routes - Friend and block management endpoints.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.core.auth.models import User
from src.api.schemas.relationships import (
    FriendRequestCreate,
    BlockCreate,
    RelationshipResponse,
    DetailedRelationshipInfo,
    PresenceInfo,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
import utils.logger as logger
from src.core.database import cached

router = APIRouter(tags=["Relationships"])


def _relationship_to_response(rel) -> RelationshipResponse:
    """Convert relationship object to response model."""
    status = getattr(rel, "status", None)
    if status is not None and hasattr(status, "value"):
        status = status.value

    return RelationshipResponse(
        user_id=SnowflakeID(getattr(rel, "user_id", 0) or getattr(rel, "target_id", 0)),
        status=status or "none",
        created_at=getattr(rel, "created_at", None),
    )


@router.get(
    "/@me",
    response_model=List[DetailedRelationshipInfo],
    summary="Get relationships",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60, prefix="relationships_api")
def get_relationships(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[DetailedRelationshipInfo]:
    """
    Get all relationships for current user (cached for 60s).

    Returns friends, pending requests, and blocked users with user info.
    """
    relationships = api.get_relationships()
    auth = api.get_auth()
    presence = api.get_presence()

    if not relationships:
        logger.error("Relationships module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Relationships module not available"}
            },
        )

    try:
        # 1. Fetch raw relationship rows in fewer passes
        try:
            rel_data = relationships.get_all_relationships(current_user.user_id)
            friends = rel_data["friends"]
            pending_in = rel_data["pending_incoming"]
            pending_out = rel_data["pending_outgoing"]
            blocked = rel_data["blocked"]
        except Exception as e:
            logger.error(
                f"Database error fetching relationships for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Failed to fetch relationships"}
                },
            )

        # 2. Collect all involved user IDs for bulk fetching
        all_user_ids = set()
        my_id = current_user.user_id
        
        friends_ids = []
        for f in friends:
            # In a friendship row, we are either user_id or friend_id. The friend is the other one.
            f_uid = getattr(f, "user_id", 0)
            f_fid = getattr(f, "friend_id", 0)
            target_id = f_fid if f_uid == my_id else f_uid
            
            friends_ids.append(target_id)
            all_user_ids.add(target_id)
            
        pending_in_ids = []
        for r in pending_in:
            # Incoming: sender is the other person
            uid = getattr(r, "sender_id", 0)
            pending_in_ids.append(uid)
            all_user_ids.add(uid)
            
        pending_out_ids = []
        for r in pending_out:
            # Outgoing: recipient is the other person
            uid = getattr(r, "recipient_id", 0)
            pending_out_ids.append(uid)
            all_user_ids.add(uid)
            
        blocked_ids = []
        for b in blocked:
            # Blocked: blocked_id is the other person (unless we are blocked_id, but usually we list who WE blocked)
            uid = getattr(b, "blocked_id", 0)
            blocked_ids.append(uid)
            all_user_ids.add(uid)

        # 3. Bulk fetch user info and presence
        user_info_map = {}
        presence_map = {}
        
        if all_user_ids:
            if auth:
                try:
                    users = auth.get_users_bulk(list(all_user_ids))
                    for uid_str, u in users.items():
                        user_info_map[uid_str] = {
                            "username": u.username,
                            "avatar_url": getattr(u, "avatar_url", None)
                        }
                except Exception as e:
                    logger.debug(f"Failed bulk user fetch: {e}")
            
            if presence:
                try:
                    # Use bulk presence fetch (internal to presence module)
                    presences = presence.get_visible_presences_bulk(my_id, list(all_user_ids))
                    for p_uid, p in presences.items():
                        p_status = getattr(p, "status", None)
                        if p_status and hasattr(p_status, "value"):
                            p_status = p_status.value
                        presence_map[p_uid] = PresenceInfo(status=p_status or "offline")
                except Exception as e:
                    logger.debug(f"Failed bulk presence fetch: {e}")

        # 4. Map back to result objects
        result = []

        for idx, f in enumerate(friends):
            uid = friends_ids[idx]
            # Robust lookup: check both string and int keys
            info = user_info_map.get(uid) or user_info_map.get(str(uid)) or {}
            result.append(
                DetailedRelationshipInfo(
                    user_id=str(uid),
                    username=info.get("username") or f"User {uid}",
                    avatar_url=info.get("avatar_url"),
                    status="friend",
                    presence=presence_map.get(uid) or presence_map.get(str(uid)) or PresenceInfo(status="offline"),
                    created_at=getattr(f, "created_at", None),
                )
            )

        for idx, r in enumerate(pending_in):
            uid = pending_in_ids[idx]
            info = user_info_map.get(uid) or user_info_map.get(str(uid)) or {}
            result.append(
                DetailedRelationshipInfo(
                    user_id=str(uid),
                    username=info.get("username") or f"User {uid}",
                    avatar_url=info.get("avatar_url"),
                    status="pending_incoming",
                    presence=presence_map.get(uid) or presence_map.get(str(uid)) or PresenceInfo(status="offline"),
                    message=getattr(r, "message", None),
                    created_at=getattr(r, "created_at", None),
                )
            )

        for idx, r in enumerate(pending_out):
            uid = pending_out_ids[idx]
            info = user_info_map.get(uid) or user_info_map.get(str(uid)) or {}
            result.append(
                DetailedRelationshipInfo(
                    user_id=str(uid),
                    username=info.get("username") or f"User {uid}",
                    avatar_url=info.get("avatar_url"),
                    status="pending_outgoing",
                    presence=presence_map.get(uid) or presence_map.get(str(uid)) or PresenceInfo(status="offline"),
                    created_at=getattr(r, "created_at", None),
                )
            )

        for idx, b in enumerate(blocked):
            uid = blocked_ids[idx]
            info = user_info_map.get(uid) or user_info_map.get(str(uid)) or {}
            result.append(
                DetailedRelationshipInfo(
                    user_id=str(uid),
                    username=info.get("username") or f"User {uid}",
                    avatar_url=info.get("avatar_url"),
                    status="blocked",
                    presence=PresenceInfo(status="offline"),
                    created_at=getattr(b, "created_at", None),
                )
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error processing relationships for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error processing relationships for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


async def _dispatch_relationship_event(
    event_type: str, user_id: int, target_id: int, data: dict
):
    """Helper to dispatch relationship events via WebSocket."""
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if ws_is_setup():
            dispatcher = get_dispatcher()
            event = Event(
                event_type=EventType.RELATIONSHIP_ADD
                if event_type == "add"
                else EventType.RELATIONSHIP_REMOVE,
                data=data,
            )
            # Send ONLY to the specified user. Callers call this twice if needed for both users.
            await dispatcher.dispatch_event(event, [user_id])
    except Exception as e:
        import utils.logger as logger

        logger.debug(f"Failed to dispatch relationship event: {e}")


@router.post(
    "",
    response_model=RelationshipResponse,
    summary="Create relationship",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID or self-request"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "User is blocked"},
        404: {"model": ErrorResponse, "description": "User not found"},
        409: {"model": ErrorResponse, "description": "Relationship already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_relationship(
    body: FriendRequestCreate, current_user: TokenInfo = Depends(get_current_user)
) -> RelationshipResponse:
    """
    Send a friend request.

    Creates a pending friend request to the specified user.
    """
    relationships = api.get_relationships()
    auth = api.get_auth()
    if not relationships:
        logger.error("Relationships module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Relationships module not available"}
            },
        )

    try:
        try:
            target_id = int(body.user_id)
        except (ValueError, TypeError):
            logger.warning(
                f"User {current_user.user_id} provided invalid target ID: {body.user_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        try:
            request = relationships.send_friend_request(
                sender_id=current_user.user_id,
                recipient_id=target_id,
                message=body.message,
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "Self" in exc_name:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot send friend request to yourself",
                        }
                    },
                )
            elif "Blocked" in exc_name:
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )
            elif "Exists" in exc_name or "Already" in exc_name:
                raise HTTPException(
                    status_code=409, detail={"error": {"code": 409, "message": str(e)}}
                )
            elif "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )

            logger.error(
                f"Failed to send friend request from {current_user.user_id} to {target_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to send friend request: {str(e)}",
                    }
                },
            )

        # Get usernames for the event
        sender_username = None
        target_username = None
        if auth:
            try:
                sender = auth.get_user(current_user.user_id)
                target = auth.get_user(target_id)
                if sender:
                    sender_username = sender.username
                if target:
                    target_username = target.username
            except Exception as e:
                logger.debug(f"Failed to get usernames for relationship event: {e}")

        # Dispatch event to recipient (incoming request)
        await _dispatch_relationship_event(
            "add",
            target_id,
            current_user.user_id,
            {
                "user_id": str(current_user.user_id),
                "username": sender_username,
                "status": "pending_incoming",
                "message": body.message,
                "created_at": getattr(request, "created_at", None),
            },
        )

        # Dispatch event to sender (outgoing request)
        await _dispatch_relationship_event(
            "add",
            current_user.user_id,
            target_id,
            {
                "user_id": str(target_id),
                "username": target_username,
                "status": "pending_outgoing",
                "created_at": getattr(request, "created_at", None),
            },
        )

        # Invalidate cache for both users
        try:
            get_relationships.invalidate(current_user=current_user)
            other_user = User(id=target_id, username="", account_type=None, permissions={}, created_at=0, updated_at=0)
            get_relationships.invalidate(current_user=other_user)
        except Exception as e:
            logger.debug(f"Failed to invalidate relationship cache in create: {e}")

        return RelationshipResponse(
            user_id=SnowflakeID(target_id),
            status="pending_outgoing",
            created_at=getattr(request, "created_at", None),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in create_relationship for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/{user_id}/accept",
    response_model=SuccessResponse,
    summary="Accept friend request",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Friend request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def accept_friend_request(
    user_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Accept a friend request.

    Accepts a pending friend request from the specified user.
    """
    relationships = api.get_relationships()
    auth = api.get_auth()
    presence = api.get_presence()
    if not relationships:
        logger.error("Relationships module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Relationships module not available"}
            },
        )

    try:
        try:
            sender_id = int(user_id)
        except (ValueError, TypeError):
            logger.warning(
                f"User {current_user.user_id} provided invalid sender ID: {user_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        try:
            pending = relationships.get_pending_requests_incoming(current_user.user_id)
            request_id = None
            
            # Try to match by user_id provided (which could be the sender's user ID or the request ID itself)
            try:
                provided_id = int(user_id)
            except (ValueError, TypeError):
                provided_id = 0

            for r in pending:
                if r.id == provided_id or r.sender_id == provided_id:
                    request_id = r.id
                    break

            if not request_id:
                logger.warning(f"Friend request from/with ID {user_id} not found for user {current_user.user_id}. Pending IDs: {[r.id for r in pending]}, Senders: {[r.sender_id for r in pending]}")
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Friend request not found"}
                    },
                )

            result = relationships.accept_friend_request(
                current_user.user_id, request_id
            )
        except HTTPException:
            raise
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Friend request not found"}
                    },
                )

            logger.error(
                f"Failed to accept friend request from {sender_id} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to accept friend request: {str(e)}",
                    }
                },
            )

        # Get user info for the events
        sender_username = None
        accepter_username = None
        sender_presence = None
        accepter_presence = None
        if auth:
            try:
                sender = auth.get_user(sender_id)
                accepter = auth.get_user(current_user.user_id)
                if sender:
                    sender_username = sender.username
                if accepter:
                    accepter_username = accepter.username
            except Exception as e:
                logger.debug(f"Failed to get user info for accept event: {e}")

        if presence:
            try:
                sp = presence.get_visible_presence(current_user.user_id, sender_id)
                ap = presence.get_visible_presence(sender_id, current_user.user_id)
                if sp:
                    status = getattr(sp, "status", None)
                    if status and hasattr(status, "value"):
                        status = status.value
                    sender_presence = {"status": status or "offline"}
                if ap:
                    status = getattr(ap, "status", None)
                    if status and hasattr(status, "value"):
                        status = status.value
                    accepter_presence = {"status": status or "offline"}
            except Exception as e:
                logger.debug(f"Failed to get presence for accept event: {e}")

        created_at = getattr(result, "updated_at", None) or getattr(
            result, "created_at", None
        )

        # Dispatch event to the original sender (they now have a friend)
        await _dispatch_relationship_event(
            "add",
            sender_id,
            current_user.user_id,
            {
                "user_id": str(current_user.user_id),
                "username": accepter_username,
                "status": "friend",
                "presence": accepter_presence,
                "created_at": created_at,
            },
        )

        # Dispatch event to the accepter (they now have a friend)
        await _dispatch_relationship_event(
            "add",
            current_user.user_id,
            sender_id,
            {
                "user_id": str(sender_id),
                "username": sender_username,
                "status": "friend",
                "presence": sender_presence,
                "created_at": created_at,
            },
        )

        # Invalidate cache for both users
        try:
            get_relationships.invalidate(current_user=current_user)
            # Create a dummy user object for cache key generation
            other_user = User(id=sender_id, username="", account_type=None, permissions={}, created_at=0, updated_at=0)
            get_relationships.invalidate(current_user=other_user)
        except Exception as e:
            logger.debug(f"Failed to invalidate relationship cache: {e}")

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in accept_friend_request for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{user_id}",
    response_model=SuccessResponse,
    summary="Remove a relationship",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Relationship not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_relationship(
    user_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Remove a relationship.

    Removes friend, declines request, or unblocks user.
    """
    relationships = api.get_relationships()
    if not relationships:
        logger.error("Relationships module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Relationships module not available"}
            },
        )

    try:
        try:
            target_id = int(user_id)
        except (ValueError, TypeError):
            logger.warning(
                f"User {current_user.user_id} provided invalid target ID: {user_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        try:
            rel = relationships.get_relationship(current_user.user_id, target_id)
            if not rel:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Relationship not found"}
                    },
                )

            status = getattr(rel, "status", None)
            if status is not None and hasattr(status, "value"):
                status = status.value

            if status == "friend":
                relationships.remove_friend(current_user.user_id, target_id)
                # Notify both users that friendship is removed
                await _dispatch_relationship_event(
                    "remove",
                    current_user.user_id,
                    target_id,
                    {
                        "user_id": str(target_id),
                    },
                )
                await _dispatch_relationship_event(
                    "remove",
                    target_id,
                    current_user.user_id,
                    {
                        "user_id": str(current_user.user_id),
                    },
                )
            elif status == "blocked":
                relationships.unblock_user(current_user.user_id, target_id)
                # Notify the unblocker
                await _dispatch_relationship_event(
                    "remove",
                    current_user.user_id,
                    target_id,
                    {
                        "user_id": str(target_id),
                    },
                )
            elif status == "pending_incoming":
                pending = relationships.get_pending_requests_incoming(
                    current_user.user_id
                )
                found = False
                for r in pending:
                    if getattr(r, "sender_id", 0) == target_id:
                        relationships.decline_friend_request(current_user.user_id, r.id)
                        # Notify both users
                        await _dispatch_relationship_event(
                            "remove",
                            current_user.user_id,
                            target_id,
                            {
                                "user_id": str(target_id),
                            },
                        )
                        await _dispatch_relationship_event(
                            "remove",
                            target_id,
                            current_user.user_id,
                            {
                                "user_id": str(current_user.user_id),
                            },
                        )
                        found = True
                        break
                if not found:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Friend request not found",
                            }
                        },
                    )
            elif status == "pending_outgoing":
                pending = relationships.get_pending_requests_outgoing(
                    current_user.user_id
                )
                found = False
                for r in pending:
                    if getattr(r, "recipient_id", 0) == target_id:
                        relationships.cancel_friend_request(current_user.user_id, r.id)
                        # Notify both users
                        await _dispatch_relationship_event(
                            "remove",
                            current_user.user_id,
                            target_id,
                            {
                                "user_id": str(target_id),
                            },
                        )
                        await _dispatch_relationship_event(
                            "remove",
                            target_id,
                            current_user.user_id,
                            {
                                "user_id": str(current_user.user_id),
                            },
                        )
                        found = True
                        break
                if not found:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Friend request not found",
                            }
                        },
                    )
            else:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Relationship not found"}
                    },
                )

            # Invalidate cache for both users
            try:
                get_relationships.invalidate(current_user=current_user)
                # Create a dummy user object for cache key generation
                other_user = User(id=target_id, username="", account_type=None, permissions={}, created_at=0, updated_at=0)
                get_relationships.invalidate(current_user=other_user)
            except Exception as e:
                logger.debug(f"Failed to invalidate relationship cache during delete: {e}")

        except HTTPException:
            raise
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Relationship not found"}
                    },
                )

            logger.error(
                f"Failed to delete relationship between {current_user.user_id} and {target_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to delete relationship: {str(e)}",
                    }
                },
            )

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_relationship for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/block",
    response_model=RelationshipResponse,
    summary="Block a user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "User not found"},
        409: {"model": ErrorResponse, "description": "Already blocked"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_user(
    body: BlockCreate, current_user: TokenInfo = Depends(get_current_user)
) -> RelationshipResponse:
    """
    Block a user.

    Blocks the specified user, removing any existing relationship.
    """
    relationships = api.get_relationships()
    if not relationships:
        logger.error("Relationships module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Relationships module not available"}
            },
        )

    try:
        try:
            target_id = int(body.user_id)
        except (ValueError, TypeError):
            logger.warning(
                f"User {current_user.user_id} provided invalid target ID for block: {body.user_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid target ID"}},
            )

        try:
            # Check if they were friends before blocking
            rel = relationships.get_relationship(current_user.user_id, target_id)
            was_friend = getattr(rel, "status", None)
            if was_friend and hasattr(was_friend, "value"):
                was_friend = was_friend.value
            was_friend = was_friend == "friend"

            block = relationships.block_user(current_user.user_id, target_id)
        except Exception as e:
            exc_name = type(e).__name__
            if "Self" in exc_name:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Cannot block yourself"}},
                )
            elif "Already" in exc_name:
                raise HTTPException(
                    status_code=409, detail={"error": {"code": 409, "message": str(e)}}
                )
            elif "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )

            logger.error(
                f"Failed to block user {target_id} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": f"Failed to block user: {str(e)}"}
                },
            )

        # Notify the blocker about the new blocked status
        await _dispatch_relationship_event(
            "add",
            current_user.user_id,
            target_id,
            {
                "user_id": str(target_id),
                "status": "blocked",
                "created_at": getattr(block, "created_at", None),
            },
        )

        # If they were friends, notify the blocked user that friendship is removed
        if was_friend:
            await _dispatch_relationship_event(
                "remove",
                target_id,
                current_user.user_id,
                {
                    "user_id": str(current_user.user_id),
                },
            )

        return RelationshipResponse(
            user_id=SnowflakeID(target_id),
            status="blocked",
            created_at=getattr(block, "created_at", None),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in block_user for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
