"""
Listing mixin - GET /@me route handler for fetching all relationships.
"""

from typing import List

from fastapi import HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.relationships import (
    DetailedRelationshipInfo,
    PresenceInfo,
)
from src.core.database import cached
import utils.logger as logger


class ListingMixin:
    @cached(ttl=60, prefix="relationships_api")
    def get_relationships(
        self,
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
                    "error": {
                        "code": 500,
                        "message": "Relationships module not available",
                    }
                },
            )

        try:
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
                        "error": {
                            "code": 500,
                            "message": "Failed to fetch relationships",
                        }
                    },
                )

            all_user_ids = set()
            my_id = current_user.user_id

            friends_ids = []
            for f in friends:
                f_uid = getattr(f, "user_id", 0)
                f_fid = getattr(f, "friend_id", 0)
                target_id = f_fid if f_uid == my_id else f_uid
                friends_ids.append(target_id)
                all_user_ids.add(target_id)

            pending_in_ids = []
            for r in pending_in:
                uid = getattr(r, "sender_id", 0)
                pending_in_ids.append(uid)
                all_user_ids.add(uid)

            pending_out_ids = []
            for r in pending_out:
                uid = getattr(r, "recipient_id", 0)
                pending_out_ids.append(uid)
                all_user_ids.add(uid)

            blocked_ids = []
            for b in blocked:
                uid = getattr(b, "blocked_id", 0)
                blocked_ids.append(uid)
                all_user_ids.add(uid)

            user_info_map = {}
            presence_map = {}

            if all_user_ids:
                if auth:
                    try:
                        users = auth.get_user_profiles_bulk(list(all_user_ids))
                        for uid_str, u in users.items():
                            user_info_map[uid_str] = {
                                "username": u.get("username"),
                                "avatar_url": u.get("avatar_url"),
                            }
                    except Exception as e:
                        logger.debug(f"Failed bulk user fetch: {e}")

                if presence:
                    try:
                        presences = presence.get_visible_presences_bulk(
                            my_id, list(all_user_ids)
                        )
                        for p_uid, p in presences.items():
                            p_status = getattr(p, "status", None)
                            if p_status and hasattr(p_status, "value"):
                                p_status = p_status.value
                            presence_map[p_uid] = PresenceInfo(
                                status=p_status or "offline"
                            )
                    except Exception as e:
                        logger.debug(f"Failed bulk presence fetch: {e}")

            result = []

            for idx, f in enumerate(friends):
                uid = friends_ids[idx]
                info = user_info_map.get(uid) or user_info_map.get(str(uid)) or {}
                result.append(
                    DetailedRelationshipInfo(
                        user_id=str(uid),
                        username=info.get("username") or f"User {uid}",
                        avatar_url=info.get("avatar_url"),
                        status="friend",
                        presence=presence_map.get(uid)
                        or presence_map.get(str(uid))
                        or PresenceInfo(status="offline"),
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
                        presence=presence_map.get(uid)
                        or presence_map.get(str(uid))
                        or PresenceInfo(status="offline"),
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
                        presence=presence_map.get(uid)
                        or presence_map.get(str(uid))
                        or PresenceInfo(status="offline"),
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
