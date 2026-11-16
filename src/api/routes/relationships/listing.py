"""
Listing mixin - GET /@me route handler for fetching all relationships.
"""

from typing import List

from fastapi import HTTPException, Depends, Query

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
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=1000000),
    ) -> List[DetailedRelationshipInfo]:
        """
        Get all relationships for current user (cached for 60s).

        Returns friends, pending requests, and blocked users with user info.

        Results are capped by ``limit`` and can be paged with ``offset``.
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

            my_id = current_user.user_id

            # Build lightweight descriptors for all relationships, then page
            # *before* hydrating user/presence data. Previously every row's id
            # was collected and both bulk lookups were issued for the entire
            # graph, even though only ``limit`` rows are returned. With a
            # populated graph that means a large IN(...) query and a full
            # presence scan per request; hydrating just the page keeps those
            # round trips proportional to the response size.
            entries = []
            for f in friends:
                f_uid = getattr(f, "user_id", 0)
                f_fid = getattr(f, "friend_id", 0)
                target_id = f_fid if f_uid == my_id else f_uid
                entries.append(
                    {
                        "uid": target_id,
                        "status": "friend",
                        "created_at": getattr(f, "created_at", None),
                    }
                )

            for r in pending_in:
                entries.append(
                    {
                        "uid": getattr(r, "sender_id", 0),
                        "status": "pending_incoming",
                        "message": getattr(r, "message", None),
                        "created_at": getattr(r, "created_at", None),
                    }
                )

            for r in pending_out:
                entries.append(
                    {
                        "uid": getattr(r, "recipient_id", 0),
                        "status": "pending_outgoing",
                        "created_at": getattr(r, "created_at", None),
                    }
                )

            for b in blocked:
                entries.append(
                    {
                        "uid": getattr(b, "blocked_id", 0),
                        "status": "blocked",
                        "created_at": getattr(b, "created_at", None),
                    }
                )

            page = entries[offset : offset + limit]

            all_user_ids = {entry["uid"] for entry in page}
            # Blocked entries always render as offline, so they never need a
            # presence lookup (their profile is still needed for name/avatar).
            presence_ids = {
                entry["uid"] for entry in page if entry["status"] != "blocked"
            }

            user_info_map = {}
            presence_map = {}

            if all_user_ids:
                if auth:
                    try:
                        users = auth.get_user_profiles_bulk(list(all_user_ids))
                        for uid_str, u in users.items():
                            user_info_map[str(uid_str)] = {
                                "username": u.get("username"),
                                "avatar_url": u.get("avatar_url"),
                            }
                    except Exception as e:
                        logger.debug(f"Failed bulk user fetch: {e}")

                if presence and presence_ids:
                    try:
                        presences = presence.get_visible_presences_bulk(
                            my_id, list(presence_ids)
                        )
                        for p_uid, p in presences.items():
                            p_status = getattr(p, "status", None)
                            if p_status and hasattr(p_status, "value"):
                                p_status = p_status.value
                            presence_map[str(p_uid)] = PresenceInfo(
                                status=p_status or "offline"
                            )
                    except Exception as e:
                        logger.debug(f"Failed bulk presence fetch: {e}")

            result = []
            for entry in page:
                uid = entry["uid"]
                status = entry["status"]
                info = user_info_map.get(str(uid)) or {}
                if status == "blocked":
                    presence_info = PresenceInfo(status="offline")
                else:
                    presence_info = presence_map.get(str(uid)) or PresenceInfo(
                        status="offline"
                    )
                data = {
                    "user_id": str(uid),
                    "username": info.get("username") or f"User {uid}",
                    "avatar_url": info.get("avatar_url"),
                    "status": status,
                    "presence": presence_info,
                    "created_at": entry["created_at"],
                }
                if status == "pending_incoming":
                    data["message"] = entry["message"]
                result.append(DetailedRelationshipInfo(**data))

            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error processing relationships for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
