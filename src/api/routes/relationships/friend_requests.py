"""
Friend request mixin - create / accept friend request route handlers.
"""

import asyncio

from fastapi import HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.relationships import (
    FriendRequestCreate,
    RelationshipResponse,
)
from src.api.schemas.common import SnowflakeID, SuccessResponse
from src.core.relationships.exceptions import (
    AlreadyFriendsError,
    FriendRequestExistsError,
    FriendRequestNotFoundError,
    PermissionDeniedError,
    SelfRelationshipError,
    UserBlockedError,
    UserNotFoundError,
)
from .base import RelationshipBaseProtocol
import utils.logger as logger


class FriendRequestMixin:
    async def create_relationship(
        self: RelationshipBaseProtocol,
        body: FriendRequestCreate,
        current_user: TokenInfo = Depends(get_current_user),
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
                    "error": {
                        "code": 500,
                        "message": "Relationships module not available",
                    }
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
            except SelfRelationshipError:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot send friend request to yourself",
                        }
                    },
                )
            except UserBlockedError as exc:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": str(exc)}},
                )
            except (FriendRequestExistsError, AlreadyFriendsError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": {"code": 409, "message": str(exc)}},
                )
            except UserNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            except Exception as e:
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

            sender_username = None
            target_username = None
            if auth:
                try:
                    profiles = auth.get_user_profiles_bulk(
                        [current_user.user_id, target_id]
                    )
                    sender = (
                        profiles.get(str(current_user.user_id)) if profiles else None
                    )
                    target = profiles.get(str(target_id)) if profiles else None
                    if sender:
                        sender_username = sender.get("username")
                    if target:
                        target_username = target.get("username")
                except Exception as e:
                    logger.debug(f"Failed to get usernames for relationship event: {e}")

            await asyncio.gather(
                self._dispatch_relationship_event(
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
                ),
                self._dispatch_relationship_event(
                    "add",
                    current_user.user_id,
                    target_id,
                    {
                        "user_id": str(target_id),
                        "username": target_username,
                        "status": "pending_outgoing",
                        "created_at": getattr(request, "created_at", None),
                    },
                ),
            )

            try:
                self._invalidate_relationship_list_cache(
                    current_user.user_id, target_id
                )
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

    async def accept_friend_request(
        self: RelationshipBaseProtocol,
        user_id: str,
        current_user: TokenInfo = Depends(get_current_user),
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
                    "error": {
                        "code": 500,
                        "message": "Relationships module not available",
                    }
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
                pending = relationships.get_pending_requests_incoming(
                    current_user.user_id
                )
                request_id = None

                try:
                    provided_id = int(user_id)
                except (ValueError, TypeError):
                    provided_id = 0

                for r in pending:
                    if r.id == provided_id or r.sender_id == provided_id:
                        request_id = r.id
                        break

                if not request_id:
                    logger.warning(
                        f"Friend request from/with ID {user_id} not found for user {current_user.user_id}. Pending IDs: {[r.id for r in pending]}, Senders: {[r.sender_id for r in pending]}"
                    )
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": {
                                "code": 404,
                                "message": "Friend request not found",
                            }
                        },
                    )

                result = relationships.accept_friend_request(
                    current_user.user_id, request_id
                )
            except HTTPException:
                raise
            except FriendRequestNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Friend request not found"}
                    },
                )
            except PermissionDeniedError as exc:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": str(exc)}},
                )
            except Exception as e:
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

            sender_username = None
            accepter_username = None
            sender_presence = None
            accepter_presence = None
            if auth:
                try:
                    profiles = auth.get_user_profiles_bulk(
                        [sender_id, current_user.user_id]
                    )
                    sender = profiles.get(str(sender_id)) if profiles else None
                    accepter = (
                        profiles.get(str(current_user.user_id)) if profiles else None
                    )
                    if sender:
                        sender_username = sender.get("username")
                    if accepter:
                        accepter_username = accepter.get("username")
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

            await asyncio.gather(
                self._dispatch_relationship_event(
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
                ),
                self._dispatch_relationship_event(
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
                ),
            )

            try:
                self._invalidate_relationship_list_cache(
                    current_user.user_id, sender_id
                )
            except Exception as e:
                logger.debug(f"Failed to invalidate relationship cache: {e}")

            return SuccessResponse(success=True, message=None)
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
