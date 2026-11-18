import utils.logger as logger
from src.api.schemas.auth import UserResponse
from src.api.schemas.common import SnowflakeID

from src.core.security.hardening import reject_control_characters


# Credential-shaped fields (challenge/reset tokens, 2FA codes, device
# fingerprints) are machine values with a fixed grammar. Bounding them at the
# HTTP trust boundary stops an anonymous caller from pushing an oversized body
# into token hashing / DB lookups before the auth core's own grammar checks
# run, and control characters have no place in a value that is hashed,
# compared in constant time, stored or written to an audit log.
MAX_CREDENTIAL_TOKEN_LENGTH = 512
MAX_2FA_CODE_LENGTH = 8


def validate_credential_input(value: object, field: str, maximum: int) -> str:
    """Return a bounded, control-character-free credential field.

    The auth core still enforces the full token grammar; this is the
    trust-boundary guard that keeps pathological input out of the core in the
    first place. ``ValueError`` is raised for empty, oversized or
    control-character-bearing values so callers can map it to a clean 400.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds maximum length of {maximum}")
    return reject_control_characters(value, field)


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
