"""
Discovery mixin - User discovery/search route handlers.
"""

from typing import Optional

from fastapi import HTTPException, Depends, status

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.users import UserPublicResponse
from src.core.security.hardening import HardeningViolation, reject_control_characters
from .helpers import _user_to_public_response, require_user_token

# M5 FIX: exact-username discovery is an enumeration oracle when unmetered.
# Throttle lookups per user; the shared cache-backed limiter degrades to
# in-memory when no Valkey is configured.
DISCOVERY_LOOKUP_LIMIT = 10
DISCOVERY_WINDOW_SECONDS = 60

# A username cannot be longer than the account validator's default cap. The
# query parameter used to be unbounded: an arbitrarily long value reached both
# ``SELECT ... WHERE username = ?`` and the error log line below, turning one
# request into a storage/log amplification primitive. Bound it here, at the
# HTTP boundary, so a direct call to this handler cannot bypass it either.
MAX_SEARCH_USERNAME_LENGTH = 32


def _check_discovery_rate_limit(user_id: int) -> None:
    try:
        from src.core.database.cache.rate_limit import check_rate_limit

        allowed, _remaining = check_rate_limit(
            f"user_discovery:{user_id}",
            DISCOVERY_LOOKUP_LIMIT,
            DISCOVERY_WINDOW_SECONDS,
        )
    except Exception as e:
        # This route is the only defence against username enumeration. If the
        # limiter cannot be evaluated the request must be denied, not allowed:
        # every other rate-limited report/search route answers 503 here, and a
        # skipped check silently reopens an unlimited enumeration oracle.
        logger.error(f"Discovery rate limit unavailable: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": 503,
                    "message": "User search temporarily unavailable",
                }
            },
        ) from e
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": {"code": 429, "message": "Too many requests"}},
            headers={"Retry-After": str(DISCOVERY_WINDOW_SECONDS)},
        )


def _normalized_username(raw: object) -> str:
    """Return a bounded, control-character-free username for a lookup.

    The account validator only accepts short ASCII names, so a longer value or
    one carrying C0/C1 controls can never match a real user. Rejecting it before
    the database and log sinks keeps a hostile query string from being stored
    or echoed verbatim.
    """
    if not isinstance(raw, str):
        raise ValueError("username must be a string")
    try:
        cleaned = reject_control_characters(raw, "username")
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc
    if not cleaned or len(cleaned) > MAX_SEARCH_USERNAME_LENGTH:
        raise ValueError("username is outside the allowed length")
    return cleaned


class DiscoveryMixin:
    async def search_user_by_username(
        self,
        username: Optional[str] = None,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> UserPublicResponse:
        # A bot token's user_id is its human owner, so leaving this handler
        # open would let a bot credential spend the owner's discovery quota
        # (and, via the shared bucket, evade its own). Every other user-router
        # handler that keys off user_id requires a real user session; this one
        # was the omission. Reject bots before the rate-limit lookup.
        require_user_token(current_user)
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
            username = _normalized_username(username)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid username"}},
            ) from None

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
