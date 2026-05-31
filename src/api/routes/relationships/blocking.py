"""
Blocking mixin - POST /block route handler.
"""

from fastapi import HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.relationships import BlockCreate, RelationshipResponse
from src.api.schemas.common import SnowflakeID
from src.core.relationships.exceptions import (
    AlreadyBlockedError,
    CannotBlockSelfError,
    UserNotFoundError,
)
from .base import RelationshipBaseProtocol
import utils.logger as logger


class BlockingMixin:
    async def block_user(
        self: RelationshipBaseProtocol,
        body: BlockCreate,
        current_user: TokenInfo = Depends(get_current_user),
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
                    f"User {current_user.user_id} provided invalid target ID for block: {body.user_id}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid target ID"}},
                )

            try:
                rel = relationships.get_relationship(current_user.user_id, target_id)
                was_friend = getattr(rel, "status", None)
                if was_friend and hasattr(was_friend, "value"):
                    was_friend = was_friend.value
                was_friend = was_friend == "friend"

                block = relationships.block_user(current_user.user_id, target_id)
            except CannotBlockSelfError:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Cannot block yourself"}},
                )
            except AlreadyBlockedError as exc:
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
                    f"Failed to block user {target_id} for user {current_user.user_id}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {
                            "code": 500,
                            "message": f"Failed to block user: {str(e)}",
                        }
                    },
                )

            await self._dispatch_relationship_event(
                "add",
                current_user.user_id,
                target_id,
                {
                    "user_id": str(target_id),
                    "status": "blocked",
                    "created_at": getattr(block, "created_at", None),
                },
            )

            if was_friend:
                await self._dispatch_relationship_event(
                    "remove",
                    target_id,
                    current_user.user_id,
                    {
                        "user_id": str(current_user.user_id),
                    },
                )

            try:
                self._invalidate_relationship_list_cache(
                    current_user.user_id, target_id
                )
            except Exception as e:
                logger.debug(f"Failed to invalidate relationship cache in block: {e}")

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
