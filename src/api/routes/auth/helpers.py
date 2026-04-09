import utils.logger as logger
from src.api.schemas.auth import UserResponse
from src.api.schemas.common import SnowflakeID


def _user_to_response(user) -> UserResponse:
    try:
        return UserResponse(
            id=SnowflakeID(user.id),
            username=user.username,
            email=getattr(user, "email", None),
            avatar_url=getattr(user, "avatar_url", None),
            created_at=user.created_at,
            email_verified=getattr(user, "email_verified", False),
            totp_enabled=getattr(user, "totp_enabled", False),
            age_verified=getattr(user, "age_verified", False),
            badges=getattr(user, "badges", []),
            deletion_status=getattr(user, "deletion_status", "active"),
            deletion_at=getattr(user, "deletion_at", None),
        )
    except Exception as e:
        logger.error(f"Error converting user object to response: {e}")
        raise e
