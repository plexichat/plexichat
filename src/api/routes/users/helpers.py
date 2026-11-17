from fastapi import HTTPException

import src.api as api
import utils.logger as logger
from src.api.schemas.auth import UserResponse
from src.api.schemas.users import UserPublicResponse
from src.api.schemas.common import SnowflakeID
from src.core.database import cached
from src.core.database.cache.serialization import generate_cache_key


def require_user_token(current_user) -> None:
    """Reject bot credentials on endpoints bound to a human account.

    A bot token's ``user_id`` is its *owner*, so ``/@me`` handlers that read or
    mutate the authenticated account would otherwise operate on the owner's
    profile, DMs, settings and deletion state using a bot's token. Only
    ``GET /@me`` intentionally supports bots (it returns the bot's own
    synthetic profile); every other handler here acts on a human account.
    """
    if getattr(current_user, "token_type", "user") != "user":
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "A user token is required for this endpoint",
                }
            },
        )


def authenticated_cache_key(prefix: str):
    """Build a cache key that partitions entries by the *credential* used.

    ``generate_cache_key`` reduces ``TokenInfo`` to ``TokenInfo:<user_id>``.
    Bot tokens carry their owner's ``user_id`` (``account_id`` is the bot), so
    a bot credential and its owner's user session collide on that key. Any
    private response cached by one — own profile, DM list, messaging settings —
    can then be served to the other. Bot credentials get a dedicated namespace
    keyed on the bot account id; user credentials keep the legacy layout so
    existing invalidation helpers continue to match.
    """

    def _builder(*args, **kwargs):
        token = kwargs.get("current_user")
        if token is None:
            token = next((a for a in args if hasattr(a, "token_type")), None)
        # Decorated handlers are bound methods; ``self`` carries no request
        # identity (and its repr is not stable) so it must not enter the key.
        passthrough = args[1:] if args and args[0] is not token else ()

        if getattr(token, "token_type", None) != "bot":
            return generate_cache_key(prefix, *passthrough, **kwargs)

        extras = [str(a) for a in passthrough if a is not token]
        extras.extend(
            f"{k}={v}" for k, v in sorted(kwargs.items()) if k != "current_user"
        )
        key = (
            f"{prefix}:bot:{getattr(token, 'account_id', '')}"
            f":{getattr(token, 'user_id', '')}"
        )
        return f"{key}:{':'.join(extras)}" if extras else key

    return _builder


def _get_attr(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _user_to_response(user, include_private: bool = False) -> UserResponse:
    try:
        user_id = int(_get_attr(user, "id") or 0)

        badges = _get_attr(user, "badges")
        if badges is None:
            badges = []
            try:
                from src.core import features

                if features._setup_complete:
                    badges = features.get_user_badges(int(user_id or 0))
            except Exception as badge_error:
                logger.debug(f"user badge lookup failed: {badge_error}")

        return UserResponse(
            id=SnowflakeID(user_id),
            username=str(_get_attr(user, "username") or ""),
            email=_get_attr(user, "email") if include_private else None,
            avatar_url=_get_attr(user, "avatar_url"),
            created_at=int(_get_attr(user, "created_at") or 0),
            email_verified=_get_attr(user, "email_verified", False)
            if include_private
            else False,
            totp_enabled=_get_attr(user, "totp_enabled", False),
            age_verified=_get_attr(user, "age_verified", False)
            if include_private
            else False,
            badges=badges,
            deletion_status=_get_attr(user, "deletion_status", "active"),
            deletion_at=_get_attr(user, "deletion_at", None),
        )
    except Exception as e:
        logger.error(f"Error converting user object to response: {e}")
        raise e


def _user_to_public_response(user) -> UserPublicResponse:
    try:
        user_id = int(_get_attr(user, "id") or 0)

        badges = _get_attr(user, "badges")

        if badges is None:
            badges = []
            try:
                from src.core import features

                if features._setup_complete:
                    badges = features.get_user_badges(user_id)
            except Exception as e:
                logger.debug(f"Failed to fetch badges for user {user_id}: {e}")

        return UserPublicResponse(
            id=SnowflakeID(user_id),
            username=str(_get_attr(user, "username") or ""),
            avatar_url=_get_attr(user, "avatar_url"),
            created_at=int(_get_attr(user, "created_at") or 0),
            badges=badges,
        )
    except Exception as e:
        logger.error(f"Error converting user object to public response: {e}")
        raise e


def _user_to_dict(user) -> dict:
    try:
        account_type = getattr(user, "account_type", None)
        if account_type is not None and hasattr(account_type, "value"):
            account_type = account_type.value
        user_id = getattr(user, "id", None)
        return {
            "id": None if user_id is None else str(user_id),
            "username": user.username,
            "email": getattr(user, "email", None),
            "avatar_url": getattr(user, "avatar_url", None),
            "created_at": user.created_at,
            "email_verified": getattr(user, "email_verified", False),
            "totp_enabled": getattr(user, "totp_enabled", False),
            "account_type": account_type,
            "permissions": getattr(user, "permissions", {}),
            "badges": getattr(user, "badges", []),
        }
    except Exception as e:
        logger.error(f"Error converting user object to dict: {e}")
        raise e


def _get_user_cached(user_id: int):
    try:
        auth = api.get_auth()
        if not auth:
            return None
        user = auth.get_user(user_id)
        return _user_to_dict(user) if user else None
    except Exception as e:
        logger.debug(f"Cache fetch failed for user {user_id}: {e}")
        return None


_get_user_cached = cached(ttl=60, prefix="user_api")(_get_user_cached)
