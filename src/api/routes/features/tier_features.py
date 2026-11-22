"""
User Features API routes - Admin endpoints for managing user features, badges, and tiers.

Admin-only endpoints for:
- Viewing/updating user features
- Managing badges
- Setting rate limit tiers

Public endpoint for users to view their own features.
"""

from fastapi import APIRouter, HTTPException, Depends, status, Request

import re

import utils.logger as logger

from src.api.middleware.authentication import get_current_user, TokenInfo
from src.core.auth.models import AccountType
from ..admin.utils import check_host_restriction, get_admin_from_token
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.features import (
    UserFeaturesResponse,
    UpdateFeaturesRequest,
    BadgeInfo,
    PublicFeaturesResponse,
    TiersResponse,
    BadgesResponse,
    TierLimitsResponse,
)
from src.api.schemas.common import ErrorResponse
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

router = APIRouter(tags=["User Features"])

# An admin may set a tier and an expiry, but the payload is still client data.
# Bound every field so a hostile admin session (or a MITM with a leaked token)
# cannot persist a megabyte-long tier name, control-character-laden notes, or a
# timestamp that turns the expiry calculation into an absurd value.
_MAX_TIER_NAME_LENGTH = 50
_MAX_FEATURE_NOTES_LENGTH = 2000
_MAX_EXPIRES_AT = 4102444800  # 2100-01-01T00:00:00Z
_TIER_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,%d}$" % _MAX_TIER_NAME_LENGTH)


# === Helper Functions ===


def _feature_user_id(raw: str) -> int:
    """Parse an admin-targeted user id inside the snowflake range."""
    try:
        return parse_id(raw, "user ID")
    except HardeningViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": str(exc)}},
        ) from exc


def _validate_feature_settings(body: UpdateFeaturesRequest) -> None:
    """Constrain the keys/values of a raw feature-settings update payload."""
    if body.rate_limit_tier is not None:
        tier = str(body.rate_limit_tier)
        # ``$`` matches just before a trailing newline in Python, so
        # ``_TIER_NAME.match("gold\n")`` used to pass and persist a tier name
        # carrying a control character into feature config and log lines.
        # Require a full match and run the shared control-character guard, the
        # same defence ``notes`` already gets below.
        if len(tier) > _MAX_TIER_NAME_LENGTH or not _TIER_NAME.fullmatch(tier):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid rate limit tier"}},
            )
        try:
            reject_control_characters(tier, "rate limit tier")
        except HardeningViolation as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(exc)}},
            ) from exc
    if body.notes is not None:
        notes = str(body.notes)
        if len(notes) > _MAX_FEATURE_NOTES_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Admin notes are too long"}},
            )
        try:
            reject_control_characters(notes, "notes")
        except HardeningViolation as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(exc)}},
            ) from exc
    if body.expires_at is not None:
        try:
            bounded_int(
                body.expires_at,
                "expires_at",
                minimum=0,
                maximum=_MAX_EXPIRES_AT,
            )
        except HardeningViolation as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(exc)}},
            ) from exc


async def get_admin_access(
    request: Request,
) -> TokenInfo:
    """
    Dependency that allows access via either a standard user token with admin permissions
    OR a dedicated admin session token.
    """
    # These routes live under /admin/* and mutate user tiers/features, so they
    # must enforce the same host-level perimeter as every other admin surface.
    # Without this, a valid admin credential could be replayed from any host,
    # bypassing the configured admin host allowlist (and the provisional-license
    # host gate) entirely. ``require_admin_ui=False`` keeps the proxy-spoofing,
    # licence and allowlist checks but does not require the standalone admin UI
    # to be enabled -- these endpoints are also driven by the web client.
    check_host_restriction(request, require_admin_ui=False)

    # 1. Try standard user first (from middleware state)
    user = request.scope.get("state", {}).get("user")
    if user:
        if (
            user.permissions.get("administrator", False)
            or user.permissions.get("admin.*", False)
            or user.permissions.get("*", False)
        ):
            return user

    # 2. Try dedicated admin session
    try:
        admin_id = get_admin_from_token(request)

        # Create synthetic TokenInfo for the admin session
        return TokenInfo(
            valid=True,
            token_type="admin",
            account_id=admin_id,
            user_id=admin_id,
            session_id=None,
            permissions={"administrator": True, "admin.*": True},
            rate_limit_tier="staff",
            expires_at=None,
            username="admin",
            account_type=AccountType.SYSTEM,
        )
    except Exception:
        # Re-raise 401 if both fail
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": 401, "message": "Admin access required"}},
        )


def _get_features_module():
    """Get features module."""
    try:
        from src.core import features

        if features.is_setup():
            return features
    except ImportError:
        pass
    return None


def _get_auth_module():
    """Get auth module."""
    try:
        from src.core import auth

        return auth
    except ImportError:
        pass
    return None


def _check_admin(token_info: TokenInfo) -> None:
    """Check if user is admin."""
    has_admin = (
        token_info.permissions.get("administrator", False)
        or token_info.permissions.get("admin.*", False)
        or token_info.permissions.get("*", False)
    )
    if not has_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Admin access required"}},
        )


def _user_exists(user_id: int) -> bool:
    """Check if user exists."""
    auth = _get_auth_module()
    if auth:
        user = auth.get_user(user_id)
        return user is not None
    return False


# === Admin Endpoints ===


@router.get(
    "/admin/users/{user_id}/features",
    response_model=UserFeaturesResponse,
    summary="Get user features (Admin)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_features(
    user_id: str, current_user: TokenInfo = Depends(get_admin_access)
) -> UserFeaturesResponse:
    """
    Get features for a specific user (admin only).

    Returns feature flags, badges, and tier information.
    """
    _check_admin(current_user)

    # Reading another account's tier/badges/feature flags is user data, not a
    # generic admin capability. ``get_admin_access`` synthesises ``admin.*``
    # for *any* valid admin session and ``_check_admin`` only tests that
    # wildcard, so a narrowly-scoped admin (e.g. tickets-only support) could
    # otherwise enumerate any account's features. The sibling admin route
    # ``GET /admin/users/{id}`` gates the same data on ``users.read``; require
    # the same scope here and fail closed when the DB or permission subsystem
    # cannot resolve the caller's role grants.
    import src.api as api

    from src.core.admin.permissions import check_admin_permission

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(current_user.user_id, "users.read", db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}},
        )

    try:
        uid = _feature_user_id(user_id)

        if not _user_exists(uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        try:
            user_features = features.get_user_features(uid)
            tier = features.get_user_tier(uid)
            tier_limits = features.get_tier_limits(tier)

            return UserFeaturesResponse(
                user_id=user_id,
                rate_limit_tier=tier,
                badges=user_features.badges if user_features else [],
                tier_limits=TierLimitsResponse(**tier_limits.to_dict()),
                expires_at=user_features.expires_at if user_features else None,
            )
        except Exception as e:
            logger.error(
                f"Failed to fetch features for user {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Failed to fetch user features",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_user_features for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.put(
    "/admin/users/{user_id}/features",
    response_model=UserFeaturesResponse,
    summary="Update user features (Admin)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID or data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_user_features(
    user_id: str,
    body: UpdateFeaturesRequest,
    current_user: TokenInfo = Depends(get_admin_access),
) -> UserFeaturesResponse:
    """
    Update features for a specific user (admin only).

    Can update feature flags, tier, and expiration.
    """
    _check_admin(current_user)
    _validate_feature_settings(body)

    # Bare admin membership is not enough to change another account's tier or
    # expiry. The admin-session path in ``get_admin_access`` synthesises
    # ``admin.*`` for *any* valid admin session, and ``_check_admin`` only
    # tests that wildcard, so a narrowly-scoped admin (e.g. a tickets-only
    # support admin) could otherwise mutate user tiers here. The sibling
    # admin route ``PUT /admin/users/{id}/tier`` gates the same mutation on
    # ``users.tier``; apply that scope here too. Fail closed when the DB is
    # unavailable or the permission subsystem cannot resolve role grants.
    import src.api as api

    from src.core.admin.permissions import check_admin_permission

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(current_user.user_id, "users.tier", db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}},
        )

    try:
        uid = _feature_user_id(user_id)

        if not _user_exists(uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        try:
            features.set_user_features(
                user_id=uid,
                admin_id=current_user.user_id,
                rate_limit_tier=body.rate_limit_tier,
                expires_at=body.expires_at,
                notes=body.notes,
            )

            logger.info(f"Admin {current_user.user_id} updated features for user {uid}")

            # Return updated features
            user_features = features.get_user_features(uid)
            tier = features.get_user_tier(uid)
            tier_limits = features.get_tier_limits(tier)

            return UserFeaturesResponse(
                user_id=user_id,
                rate_limit_tier=tier,
                badges=user_features.badges if user_features else [],
                tier_limits=TierLimitsResponse(**tier_limits.to_dict()),
                expires_at=user_features.expires_at if user_features else None,
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "InvalidTier" in exc_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": f"Invalid tier: {e}"}},
                )
            logger.error(
                f"Failed to update features for user {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": {"code": 500, "message": "Failed to update user features"}
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in update_user_features for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/admin/tiers",
    response_model=TiersResponse,
    summary="Get available tiers (Admin)",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_available_tiers(
    current_user: TokenInfo = Depends(get_admin_access),
) -> TiersResponse:
    """
    Get all available rate limit tiers (admin only).
    """
    _check_admin(current_user)

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}},
        )

    try:
        # The tier table is derived from a largely-static config section.
        # ``get_tier_configuration`` builds/validates it once and reuses it
        # until the config changes, so a listing request no longer re-reads
        # config and revalidates every limit on each call.
        tier_config = features.get_tier_configuration()
        tier_info = {
            tier: TierLimitsResponse(**limits.to_dict())
            for tier, limits in tier_config.limits.items()
        }

        return TiersResponse(tiers=tier_info, default=tier_config.default)
    except Exception as e:
        logger.error(f"Failed to fetch available tiers: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {"code": 500, "message": "Failed to fetch tier configuration"}
            },
        )


@router.get(
    "/admin/badges",
    response_model=BadgesResponse,
    summary="Get available badges (Admin)",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_available_badges(
    current_user: TokenInfo = Depends(get_admin_access),
) -> BadgesResponse:
    """
    Get all available badges (admin only).
    """
    _check_admin(current_user)

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}},
        )

    try:
        from src.core.features.models import Badge, AVAILABLE_BADGES

        badges = []
        for badge_name in AVAILABLE_BADGES:
            badge_info = Badge.get_badge_info(badge_name)
            badges.append(
                BadgeInfo(
                    name=badge_info.name,
                    display_name=badge_info.display_name,
                    description=badge_info.description,
                    icon=badge_info.icon,
                    color=badge_info.color,
                )
            )

        return BadgesResponse(badges=badges)
    except Exception as e:
        logger.error(f"Failed to fetch available badges: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {"code": 500, "message": "Failed to fetch badge configuration"}
            },
        )


# === Public Endpoints ===


@router.get(
    "/users/@me/features",
    response_model=PublicFeaturesResponse,
    summary="Get my features",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_my_features(
    current_user: TokenInfo = Depends(get_current_user),
) -> PublicFeaturesResponse:
    """
    Get current user's features and badges.

    Returns badges with display info and tier limits.
    """
    require_user_token(current_user)
    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}},
        )

    try:
        from src.core.features.models import Badge

        user_features = features.get_user_features(current_user.user_id)
        tier = features.get_user_tier(current_user.user_id)
        tier_limits = features.get_tier_limits(tier)

        badge_names = user_features.badges if user_features else []
        badges = []
        for badge_name in badge_names:
            badge_info = Badge.get_badge_info(badge_name)
            badges.append(
                BadgeInfo(
                    name=badge_info.name,
                    display_name=badge_info.display_name,
                    description=badge_info.description,
                    icon=badge_info.icon,
                    color=badge_info.color,
                )
            )

        return PublicFeaturesResponse(
            badges=badges,
            tier=tier,
            tier_limits=TierLimitsResponse(**tier_limits.to_dict()),
        )
    except Exception as e:
        logger.error(
            f"Failed to fetch features for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Failed to fetch features"}},
        )
