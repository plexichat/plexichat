"""
User Features API routes - Admin endpoints for managing user features, badges, and tiers.

Admin-only endpoints for:
- Viewing/updating user features
- Managing badges
- Setting rate limit tiers

Public endpoint for users to view their own features.
"""

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

import utils.logger as logger
import utils.config as config

from src.api.middleware.authentication import get_current_user, TokenInfo

router = APIRouter()


# === Request/Response Models ===

class UserFeaturesResponse(BaseModel):
    """Response for user features."""
    user_id: str
    can_create_org: bool
    rate_limit_tier: str
    badges: List[str]
    tier_limits: Dict[str, Any]
    expires_at: Optional[int] = None


class UpdateFeaturesRequest(BaseModel):
    """Request to update user features."""
    can_create_org: Optional[bool] = None
    rate_limit_tier: Optional[str] = None
    expires_at: Optional[int] = None
    notes: Optional[str] = None


class SetTierRequest(BaseModel):
    """Request to set user tier."""
    tier: str = Field(..., min_length=1, max_length=50)
    expires_at: Optional[int] = None


class BadgeInfo(BaseModel):
    """Badge information."""
    name: str
    display_name: str
    description: str
    icon: str
    color: str


class PublicFeaturesResponse(BaseModel):
    """Public response for user's own features."""
    badges: List[BadgeInfo]
    tier: str
    tier_limits: Dict[str, Any]


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

@router.get("/admin/users/{user_id}/features", response_model=UserFeaturesResponse)
async def get_user_features(
    user_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get features for a specific user (admin only).
    
    Returns feature flags, badges, and tier information.
    """
    _check_admin(current_user)
    
    features = _get_features_module()
    if not features:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )
    
    try:
        uid = int(user_id)
    except ValueError:
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
            can_create_org=user_features.can_create_org if user_features else False,
            rate_limit_tier=tier,
            badges=user_features.badges if user_features else [],
            tier_limits=tier_limits.to_dict(),
            expires_at=user_features.expires_at if user_features else None
        )
    except Exception as e:
        logger.error(f"Failed to get features for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put("/admin/users/{user_id}/features", response_model=UserFeaturesResponse)
async def update_user_features(
    user_id: str,
    body: UpdateFeaturesRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Update features for a specific user (admin only).
    
    Can update feature flags, tier, and expiration.
    """
    _check_admin(current_user)
    
    features = _get_features_module()
    if not features:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )
    
    try:
        uid = int(user_id)
    except ValueError:
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
            can_create_org=body.can_create_org,
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
            can_create_org=user_features.can_create_org if user_features else False,
            rate_limit_tier=tier,
            badges=user_features.badges if user_features else [],
            tier_limits=tier_limits.to_dict(),
            expires_at=user_features.expires_at if user_features else None
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "InvalidTier" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(e)}}
            )
        logger.error(f"Failed to update features for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put("/admin/users/{user_id}/tier", response_model=UserFeaturesResponse)
async def set_user_tier(
    user_id: str,
    body: SetTierRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Set rate limit tier for a user (admin only).
    """
    _check_admin(current_user)
    
    features = _get_features_module()
    if not features:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )
    
    try:
        uid = int(user_id)
    except ValueError:
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
            can_create_org=user_features.can_create_org if user_features else False,
            rate_limit_tier=tier,
            badges=user_features.badges if user_features else [],
            tier_limits=tier_limits.to_dict(),
            expires_at=user_features.expires_at if user_features else None
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "InvalidTier" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(e)}}
            )
        logger.error(f"Failed to set tier for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/admin/users/{user_id}/badges/{badge}")
async def add_user_badge(
    user_id: str,
    badge: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Add a badge to a user (admin only).
    """
    _check_admin(current_user)
    
    features = _get_features_module()
    if not features:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )
    
    try:
        uid = int(user_id)
    except ValueError:
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
        
        return {"success": True, "badges": badges}
    except Exception as e:
        exc_name = type(e).__name__
        if "InvalidBadge" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(e)}}
            )
        logger.error(f"Failed to add badge to user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete("/admin/users/{user_id}/badges/{badge}")
async def remove_user_badge(
    user_id: str,
    badge: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Remove a badge from a user (admin only).
    """
    _check_admin(current_user)
    
    features = _get_features_module()
    if not features:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )
    
    try:
        uid = int(user_id)
    except ValueError:
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
        
        return {"success": True, "badges": badges}
    except Exception as e:
        logger.error(f"Failed to remove badge from user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/admin/tiers")
async def get_available_tiers(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all available rate limit tiers (admin only).
    """
    _check_admin(current_user)
    
    features = _get_features_module()
    if not features:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )
    
    try:
        tiers = features.get_available_tiers()
        tier_info = {}
        for tier in tiers:
            limits = features.get_tier_limits(tier)
            tier_info[tier] = limits.to_dict()
        
        return {"tiers": tier_info, "default": features.get_default_tier()}
    except Exception as e:
        logger.error(f"Failed to get tiers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/admin/badges")
async def get_available_badges(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all available badges (admin only).
    """
    _check_admin(current_user)
    
    features = _get_features_module()
    if not features:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Features module not available"}}
        )
    
    try:
        from src.core.features.models import Badge, AVAILABLE_BADGES
        
        badges = []
        for badge_name in AVAILABLE_BADGES:
            badge_info = Badge.get_badge_info(badge_name)
            badges.append({
                "name": badge_info.name,
                "display_name": badge_info.display_name,
                "description": badge_info.description,
                "icon": badge_info.icon,
                "color": badge_info.color
            })
        
        return {"badges": badges}
    except Exception as e:
        logger.error(f"Failed to get badges: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# === Public Endpoints ===

@router.get("/users/@me/features", response_model=PublicFeaturesResponse)
async def get_my_features(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get current user's features and badges.
    
    Returns badges with display info and tier limits.
    """
    features = _get_features_module()
    if not features:
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
            tier_limits=tier_limits.to_dict()
        )
    except Exception as e:
        logger.error(f"Failed to get features for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )
