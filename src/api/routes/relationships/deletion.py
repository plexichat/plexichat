"""
Deletion mixin - DELETE /{user_id} route handler.
"""

from fastapi import HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SuccessResponse
from src.core.relationships.exceptions import (
    FriendRequestNotFoundError,
    NotBlockedError,
    NotFriendsError,
    PermissionDeniedError,
)
from .base import RelationshipBaseProtocol
import utils.logger as logger


class DeletionMixin:
    async def delete_relationship(
        self: RelationshipBaseProtocol,
        user_id: str,
        current_user: TokenInfo = Depends(get_current_user),
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
                    "error": {
                        "code": 500,
                        "message": "Relationships module not available",
                    }
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
                if status is None and isinstance(rel, dict):
                    status = rel.get("status")
                if status is not None and hasattr(status, "value"):
                    status = status.value

                status_str = str(status).lower() if status is not None else ""
                if "." in status_str:
                    status_str = status_str.split(".")[-1]

                if status_str in ("friend", "friends"):
                    relationships.remove_friend(current_user.user_id, target_id)
                    await self._dispatch_relationship_event(
                        "remove",
                        current_user.user_id,
                        target_id,
                        {
                            "user_id": str(target_id),
                        },
                    )
                    await self._dispatch_relationship_event(
                        "remove",
                        target_id,
                        current_user.user_id,
                        {
                            "user_id": str(current_user.user_id),
                        },
                    )
                elif status_str == "blocked":
                    relationships.unblock_user(current_user.user_id, target_id)
                    await self._dispatch_relationship_event(
                        "remove",
                        current_user.user_id,
                        target_id,
                        {
                            "user_id": str(target_id),
                        },
                    )
                elif status_str == "pending_incoming":
                    pending = relationships.get_pending_requests_incoming(
                        current_user.user_id
                    )
                    found = False
                    for r in pending:
                        if getattr(r, "sender_id", 0) == target_id:
                            relationships.decline_friend_request(
                                current_user.user_id, r.id
                            )
                            await self._dispatch_relationship_event(
                                "remove",
                                current_user.user_id,
                                target_id,
                                {
                                    "user_id": str(target_id),
                                },
                            )
                            await self._dispatch_relationship_event(
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
                elif status_str == "pending_outgoing":
                    pending = relationships.get_pending_requests_outgoing(
                        current_user.user_id
                    )
                    found = False
                    for r in pending:
                        if getattr(r, "recipient_id", 0) == target_id:
                            relationships.cancel_friend_request(
                                current_user.user_id, r.id
                            )
                            await self._dispatch_relationship_event(
                                "remove",
                                current_user.user_id,
                                target_id,
                                {
                                    "user_id": str(target_id),
                                },
                            )
                            await self._dispatch_relationship_event(
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
                    try:
                        friends = relationships.get_friends(current_user.user_id)
                        if any(
                            int(getattr(f, "friend_id", 0)) == int(target_id)
                            for f in friends
                        ):
                            relationships.remove_friend(current_user.user_id, target_id)
                        else:
                            raise HTTPException(
                                status_code=404,
                                detail={
                                    "error": {
                                        "code": 404,
                                        "message": "Relationship not found",
                                    }
                                },
                            )
                    except HTTPException:
                        raise

                try:
                    self._invalidate_relationship_list_cache(
                        current_user.user_id, target_id
                    )
                except Exception as e:
                    logger.debug(
                        f"Failed to invalidate relationship cache during delete: {e}"
                    )

            except HTTPException:
                raise
            except (FriendRequestNotFoundError, NotBlockedError, NotFriendsError):
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {"code": 404, "message": "Relationship not found"}
                    },
                )
            except PermissionDeniedError as exc:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": str(exc)}},
                )
            except Exception as e:
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

            return SuccessResponse(success=True, message=None)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in delete_relationship for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )
