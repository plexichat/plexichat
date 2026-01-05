from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any

class BadgeInfo(BaseModel):
    """Badge information."""
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="Internal badge name")
    display_name: str = Field(..., description="Display name for the badge")
    description: str = Field(..., description="Description of the badge")
    icon: str = Field(..., description="Icon name or URL")
    color: str = Field(..., description="Hex color code for the badge")

class TierLimitsResponse(BaseModel):
    """Rate limit tier configuration details."""
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="Tier name")
    multiplier: float = Field(..., description="Multiplier for rate limits")
    max_voice_minutes_per_day: int = Field(..., description="Max voice minutes per day")
    max_video_minutes_per_day: int = Field(..., description="Max video minutes per day")
    max_file_uploads_per_day: int = Field(..., description="Max file uploads per day")
    max_file_size_mb: int = Field(..., description="Max file size in MB")
    max_servers: int = Field(..., description="Max servers user can join")
    max_message_length: int = Field(..., description="Max characters per message")
    max_reactions_per_message: int = Field(..., description="Max reactions per message")
    max_pins_per_channel: int = Field(..., description="Max pins per channel")
    custom_emoji_slots: int = Field(..., description="Number of custom emoji slots")

class UserFeaturesResponse(BaseModel):
    """Response for user features (admin view)."""
    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(..., description="User ID")
    rate_limit_tier: str = Field(..., description="Current rate limit tier")
    badges: List[str] = Field(..., description="List of badge names")
    tier_limits: TierLimitsResponse = Field(..., description="Rate limit values for the tier")
    expires_at: Optional[int] = Field(None, description="Expiration timestamp (Unix)")

class PublicFeaturesResponse(BaseModel):
    """Public response for user's own features."""
    model_config = ConfigDict(from_attributes=True)

    badges: List[BadgeInfo] = Field(..., description="List of badges with details")
    tier: str = Field(..., description="Current user tier")
    tier_limits: TierLimitsResponse = Field(..., description="Rate limit values for the tier")

class UpdateFeaturesRequest(BaseModel):
    """Request to update user features."""
    rate_limit_tier: Optional[str] = Field(None, description="New rate limit tier")
    expires_at: Optional[int] = Field(None, description="New expiration timestamp (Unix)")
    notes: Optional[str] = Field(None, description="Admin notes for the change")

class SetTierRequest(BaseModel):
    """Request to set user tier."""
    tier: str = Field(..., min_length=1, max_length=50, description="Tier name")
    expires_at: Optional[int] = Field(None, description="Expiration timestamp (Unix)")

class UserBadgeUpdateResponse(BaseModel):
    """Response for user badge update."""
    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether update was successful")
    badges: List[str] = Field(..., description="Updated list of badges")

class TiersResponse(BaseModel):
    """Response for available tiers."""
    model_config = ConfigDict(from_attributes=True)

    tiers: Dict[str, TierLimitsResponse] = Field(..., description="Tier names mapped to their limits")
    default: str = Field(..., description="Default tier name")

class BadgesResponse(BaseModel):
    """Response for available badges."""
    model_config = ConfigDict(from_attributes=True)

    badges: List[BadgeInfo] = Field(..., description="List of available badges with details")
