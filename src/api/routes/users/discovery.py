"""
Discovery mixin - User discovery/search route handlers.
"""

from typing import Optional

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.users import UserPublicResponse
from .helpers import _user_to_public_response

# M5 FIX: exact-username discovery is an enumeration oracle when unmetered.
# Throttle lookups per user; the shared cache-backed limiter degrades to
# in-memory when no Valkey is configured.
DISCOVERY_LOOKUP_LIMIT = 10
DISCOVERY_WINDOW_SECONDS = 60


def _check_discovery_rate_limit(user_id: int) -> None:
    try:
        from src.core.database.cache.rate_limit import check_rate_limit

        allowed, _remaining = check_rate_limit(
            f"user_discovery:{user_id}",
            DISCOVERY_LOOKUP_LIMIT,
            DISCOVERY_WINDOW_SECONDS,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Discovery rate limit unavailable, skipping check: {e}")
        return
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": {"code": 429, "message": "Too many requests"}},
            headers={"Retry-After": str(DISCOVERY_WINDOW_SECONDS)},
        )


class DiscoveryMixin:
    async def search_user_by_username(
        self,
        username: Optional[str] = None,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> UserPublicResponse:
        auth = api.get_auth()
        if not auth:
            logger.error("Auth module not available")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Auth module not available"}},
            )

        if not username:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Username required"}},
            )

        _check_discovery_rate_limit(current_user.user_id)

        try:
            user = auth.get_user_by_username(username)
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            return _user_to_public_response(user)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Search failed for username '{username}': {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
