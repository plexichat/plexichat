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

        try:
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
                if "NotFound" in type(e).__name__:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "User not found"}},
                    )

                logger.error(
                    f"Search failed for username '{username}': {e}", exc_info=True
                )
                raise HTTPException(
                    status_code=500, detail={"error": {"code": 500, "message": str(e)}}
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in search_user_by_username for '{username}': {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
