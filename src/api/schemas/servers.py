"""
Server schemas - Request/response models for server/guild endpoints.
"""

from typing import Optional, List, Dict
from pydantic import BaseModel, Field, ConfigDict

from .common import SnowflakeID


class ServerCreateRequest(BaseModel):
    """Server creation request."""
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=2, max_length=100, description="Server name")
    description: Optional[str] = Field(None, max_length=1000, description="Server description")
    icon_url: Optional[str] = Field(None, description="Server icon URL")


class ServerUpdateRequest(BaseModel):
    """Server update request."""
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, min_length=2, max_length=100, description="Server name")
    description: Optional[str] = Field(None, max_length=1000, description="Server description")
    icon_url: Optional[str] = Field(None, description="Server icon URL")
    default_channel_id: Optional[SnowflakeID] = Field(None, description="Default channel ID to select when joining")


class ServerResponse(BaseModel):
    """Server information response."""
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Server ID")
    name: str = Field(..., description="Server name")
    description: Optional[str] = Field(None, description="Server description")
    icon_url: Optional[str] = Field(None, description="Server icon URL")
    owner_id: SnowflakeID = Field(..., description="Owner user ID")
    member_count: int = Field(0, description="Number of members")
    default_channel_id: Optional[SnowflakeID] = Field(None, description="Default channel ID")
    created_at: int = Field(..., description="Creation timestamp")


class ChannelResponse(BaseModel):
    """Channel information response."""
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Channel ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    name: str = Field(..., description="Channel name")
    channel_type: str = Field(..., description="Channel type: text, voice, category")
    topic: Optional[str] = Field(None, description="Channel topic")
    position: int = Field(0, description="Channel position")
    category_id: Optional[SnowflakeID] = Field(None, description="Parent category ID")
    nsfw: bool = Field(False, description="NSFW flag")
    slowmode_seconds: int = Field(0, description="Slowmode delay in seconds")
    created_at: int = Field(..., description="Creation timestamp")


class ChannelUpdateRequest(BaseModel):
    """Channel update request."""
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Channel name")
    topic: Optional[str] = Field(None, max_length=1024, description="Channel topic")
    position: Optional[int] = Field(None, ge=0, description="Channel position")
    nsfw: Optional[bool] = Field(None, description="NSFW flag")
    slowmode_seconds: Optional[int] = Field(None, ge=0, le=21600, description="Slowmode delay")


class RoleResponse(BaseModel):
    """Role information response."""
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Role ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    name: str = Field(..., description="Role name")
    color: Optional[str] = Field(None, description="Role color hex")
    position: int = Field(0, description="Role position")
    permissions: Dict[str, bool] = Field(default_factory=dict, description="Role permissions")
    hoist: bool = Field(False, description="Display separately in member list")
    mentionable: bool = Field(False, description="Can be mentioned")


class MemberResponse(BaseModel):
    """Server member response."""
    model_config = ConfigDict(from_attributes=True)

    user_id: SnowflakeID = Field(..., description="User ID")
    server_id: SnowflakeID = Field(..., description="Server ID")
    nickname: Optional[str] = Field(None, description="Server nickname")
    roles: List[str] = Field(default_factory=list, description="Role IDs")
    joined_at: int = Field(..., description="Join timestamp")


class InviteResponse(BaseModel):
    """Server invite response."""
    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="Invite code")
    server_id: SnowflakeID = Field(..., description="Server ID")
    channel_id: SnowflakeID = Field(..., description="Channel ID")
    inviter_id: SnowflakeID = Field(..., description="Inviter user ID")
    uses: int = Field(0, description="Number of uses")
    max_uses: int = Field(0, description="Maximum uses (0 = unlimited)")
    max_age: int = Field(0, description="Max age in seconds (0 = never expires)")
    temporary: bool = Field(False, description="Temporary membership")
    created_at: int = Field(..., description="Creation timestamp")
    expires_at: Optional[int] = Field(None, description="Expiration timestamp")
