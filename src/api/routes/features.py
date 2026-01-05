"""
User Features API routes - Admin endpoints for managing user features, badges, and tiers.

Admin-only endpoints for:
- Viewing/updating user features
- Managing badges
- Setting rate limit tiers

Public endpoint for users to view their own features.
"""

from fastapi import APIRouter, HTTPException, Depends, status
from typing import Optional, List, Dict, Any

import utils.logger as logger

from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.features import (
    UserFeaturesResponse,
    UpdateFeaturesRequest,
    SetTierRequest,
    BadgeInfo,
    PublicFeaturesResponse,
    UserBadgeUpdateResponse,
    TiersResponse,
    BadgesResponse
)
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["User Features"])


# === Helper Functions ===

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
    # Check for admin permission
    if not token_info.permissions.get("administrator", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Admin access required"}}
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
    user_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> UserFeaturesResponse:
    """
    Get features for a specific user (admin only).
    
    Returns feature flags, badges, and tier information.
    """
    _check_admin(current_user)

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format for features request: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        if not _user_exists(uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        try:
            user_features = features.get_user_features(uid)
            tier = features.get_user_tier(uid)
            tier_limits = features.get_tier_limits(tier)

            return UserFeaturesResponse(
                user_id=user_id,
                rate_limit_tier=tier,
                badges=user_features.badges if user_features else [],
                tier_limits=tier_limits,
                expires_at=user_features.expires_at if user_features else None
            )
        except Exception as e:
            logger.error(f"Failed to fetch features for user {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": f"Failed to fetch features: {str(e)}"}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_user_features for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
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
    current_user: TokenInfo = Depends(get_current_user)
) -> UserFeaturesResponse:
    """
    Update features for a specific user (admin only).
    
    Can update feature flags, tier, and expiration.
    """
    _check_admin(current_user)

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format for features update: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        if not _user_exists(uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        try:
            features.set_user_features(
                user_id=uid,
                admin_id=current_user.user_id,
                rate_limit_tier=body.rate_limit_tier,
                expires_at=body.expires_at,
                notes=body.notes
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
                tier_limits=tier_limits,
                expires_at=user_features.expires_at if user_features else None
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "InvalidTier" in exc_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": str(e)}}
                )
            logger.error(f"Failed to update features for user {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": f"Update failed: {str(e)}"}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in update_user_features for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/admin/users/{user_id}/tier",
    response_model=UserFeaturesResponse,
    summary="Set user tier (Admin)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID or tier"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_user_tier(
    user_id: str,
    body: SetTierRequest,
    current_user: TokenInfo = Depends(get_current_user)
) -> UserFeaturesResponse:
    """
    Set rate limit tier for a user (admin only).
    """
    _check_admin(current_user)

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format for tier update: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        if not _user_exists(uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        try:
            features.set_user_tier(uid, current_user.user_id, body.tier, body.expires_at)

            logger.info(f"Admin {current_user.user_id} set tier '{body.tier}' for user {uid}")

            # Return updated features
            user_features = features.get_user_features(uid)
            tier = features.get_user_tier(uid)
            tier_limits = features.get_tier_limits(tier)

            return UserFeaturesResponse(
                user_id=user_id,
                rate_limit_tier=tier,
                badges=user_features.badges if user_features else [],
                tier_limits=tier_limits,
                expires_at=user_features.expires_at if user_features else None
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "InvalidTier" in exc_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": str(e)}}
                )
            logger.error(f"Failed to set tier for user {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": f"Failed to set tier: {str(e)}"}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in set_user_tier for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/admin/users/{user_id}/badges/{badge}",
    response_model=UserBadgeUpdateResponse,
    summary="Add user badge (Admin)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID or badge"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_user_badge(
    user_id: str,
    badge: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> UserBadgeUpdateResponse:
    """
    Add a badge to a user (admin only).
    """
    _check_admin(current_user)

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format for badge add: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        if not _user_exists(uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        try:
            badges = features.add_badge(uid, current_user.user_id, badge)

            logger.info(f"Admin {current_user.user_id} added badge '{badge}' to user {uid}")

            return UserBadgeUpdateResponse(success=True, badges=badges)
        except Exception as e:
            exc_name = type(e).__name__
            if "InvalidBadge" in exc_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": 400, "message": str(e)}}
                )
            logger.error(f"Failed to add badge to user {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": f"Failed to add badge: {str(e)}"}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in add_user_badge for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/admin/users/{user_id}/badges/{badge}",
    response_model=UserBadgeUpdateResponse,
    summary="Remove user badge (Admin)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID or badge"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_user_badge(
    user_id: str,
    badge: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> UserBadgeUpdateResponse:
    """
    Remove a badge from a user (admin only).
    """
    _check_admin(current_user)

    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format for badge removal: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid user ID"}}
            )

        if not _user_exists(uid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "User not found"}}
            )

        try:
            badges = features.remove_badge(uid, current_user.user_id, badge)

            logger.info(f"Admin {current_user.user_id} removed badge '{badge}' from user {uid}")

            return UserBadgeUpdateResponse(success=True, badges=badges)
        except Exception as e:
            logger.error(f"Failed to remove badge from user {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": f"Failed to remove badge: {str(e)}"}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in remove_user_badge for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
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
    current_user: TokenInfo = Depends(get_current_user)
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
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )

    try:
        tiers = features.get_available_tiers()
        tier_info = {tier: features.get_tier_limits(tier) for tier in tiers}

        return TiersResponse(tiers=tier_info, default=features.get_default_tier())
    except Exception as e:
        logger.error(f"Failed to fetch available tiers: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": f"Failed to fetch tiers: {str(e)}"}}
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
    current_user: TokenInfo = Depends(get_current_user)
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
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )

    try:
        from src.core.features.models import Badge, AVAILABLE_BADGES

        badges = []
        for badge_name in AVAILABLE_BADGES:
            badge_info = Badge.get_badge_info(badge_name)
            badges.append(BadgeInfo(
                name=badge_info.name,
                display_name=badge_info.display_name,
                description=badge_info.description,
                icon=badge_info.icon,
                color=badge_info.color
            ))

        return BadgesResponse(badges=badges)
    except Exception as e:
        logger.error(f"Failed to fetch available badges: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": f"Failed to fetch badges: {str(e)}"}}
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
    current_user: TokenInfo = Depends(get_current_user)
) -> PublicFeaturesResponse:
    """
    Get current user's features and badges.
    
    Returns badges with display info and tier limits.
    """
    features = _get_features_module()
    if not features:
        logger.error("Features module not available")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
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
            badges.append(BadgeInfo(
                name=badge_info.name,
                display_name=badge_info.display_name,
                description=badge_info.description,
                icon=badge_info.icon,
                color=badge_info.color
            ))

        return PublicFeaturesResponse(
            badges=badges,
            tier=tier,
            tier_limits=tier_limits
        )
    except Exception as e:
        logger.error(f"Failed to fetch features for user {current_user.user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": f"Failed to fetch features: {str(e)}"}}
        )
