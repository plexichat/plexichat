"""
Pydantic validation models for query builder.

Provides type-safe models for validating data before database operations.
These models are used with the QueryBuilder.validate() method to ensure
data integrity at the Python level before SQL execution.
"""

from pydantic import BaseModel, Field, EmailStr, field_validator, ConfigDict
from typing import Optional
from datetime import datetime


# ============================================================================
# Authentication Models
# ============================================================================

class UserInsert(BaseModel):
    """Model for inserting users into auth_users table."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "alice",
                "email": "alice@example.com",
                "password_hash": "$argon2id$v=19$m=19456,t=2,p=1$...",
            }
        }
    )
    
    username: str = Field(min_length=3, max_length=32, description="User's login username")
    email: EmailStr = Field(description="User's email address")
    password_hash: str = Field(min_length=60, description="Argon2id hashed password")


class UserUpdate(BaseModel):
    """Model for updating users in auth_users table."""
    username: Optional[str] = Field(None, min_length=3, max_length=32)
    email: Optional[EmailStr] = None
    password_hash: Optional[str] = Field(None, min_length=60)
    last_login: Optional[datetime] = None
    
    @field_validator('username', 'email', 'password_hash', mode='before')
    @classmethod
    def reject_empty_strings(cls, v):
        """Reject empty strings."""
        if isinstance(v, str) and v == "":
            raise ValueError("Cannot set empty string")
        return v


class SessionInsert(BaseModel):
    """Model for inserting sessions into sessions table."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": 1,
                "token_hash": "abc123def456...",
                "expires_at": "2026-01-18T12:00:00Z",
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0...",
            }
        }
    )
    
    user_id: int = Field(gt=0, description="ID of user who owns session")
    token_hash: str = Field(min_length=60, description="SHA256 hash of session token")
    expires_at: datetime = Field(description="Session expiration timestamp")
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class SessionUpdate(BaseModel):
    """Model for updating sessions."""
    last_activity: Optional[datetime] = None
    ip_address: Optional[str] = None


# ============================================================================
# Server Models
# ============================================================================

class ServerInsert(BaseModel):
    """Model for inserting servers."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "owner_id": 1,
                "name": "My Awesome Server",
                "icon_url": "https://example.com/icon.png",
                "description": "A place for friends",
            }
        }
    )
    
    owner_id: int = Field(gt=0, description="ID of server owner")
    name: str = Field(min_length=1, max_length=100, description="Server name")
    icon_url: Optional[str] = None
    description: Optional[str] = Field(None, max_length=500)


class ServerUpdate(BaseModel):
    """Model for updating servers."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    icon_url: Optional[str] = None
    description: Optional[str] = Field(None, max_length=500)


# ============================================================================
# Channel Models
# ============================================================================

class ChannelInsert(BaseModel):
    """Model for inserting channels."""
    server_id: int = Field(gt=0, description="ID of parent server")
    name: str = Field(min_length=1, max_length=100, description="Channel name")
    channel_type: str = Field(description="Type of channel (text, voice, category)")
    position: int = Field(default=0, ge=0, description="Display position in server")
    topic: Optional[str] = Field(None, max_length=500)
    
    @field_validator('channel_type', mode='after')
    @classmethod
    def validate_channel_type(cls, v):
        """Validate channel type is one of allowed values."""
        allowed = {'text', 'voice', 'category', 'announcement'}
        if v not in allowed:
            raise ValueError(f"channel_type must be one of {allowed}, got {v}")
        return v


class ChannelUpdate(BaseModel):
    """Model for updating channels."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    topic: Optional[str] = Field(None, max_length=500)
    position: Optional[int] = Field(None, ge=0)


# ============================================================================
# Message Models
# ============================================================================

class MessageInsert(BaseModel):
    """Model for inserting messages."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "channel_id": 1,
                "author_id": 1,
                "content": "Hello everyone!",
            }
        }
    )
    
    channel_id: int = Field(gt=0, description="ID of channel where message is posted")
    author_id: int = Field(gt=0, description="ID of message author")
    content: str = Field(min_length=1, max_length=4000, description="Message text content")


class MessageUpdate(BaseModel):
    """Model for updating messages."""
    content: str = Field(min_length=1, max_length=4000)
    edited_at: Optional[datetime] = None


# ============================================================================
# Member/Relationship Models
# ============================================================================

class ServerMemberInsert(BaseModel):
    """Model for inserting server members."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_id": 1,
                "user_id": 2,
                "joined_at": "2026-01-11T10:00:00Z",
                "nickname": "Alice",
            }
        }
    )
    
    server_id: int = Field(gt=0, description="ID of server")
    user_id: int = Field(gt=0, description="ID of user joining server")
    joined_at: Optional[datetime] = None
    nickname: Optional[str] = Field(None, max_length=32)


class ServerMemberUpdate(BaseModel):
    """Model for updating server members."""
    nickname: Optional[str] = Field(None, max_length=32)
    last_activity: Optional[datetime] = None


# ============================================================================
# Relationship/Friendship Models
# ============================================================================

class FriendshipInsert(BaseModel):
    """Model for inserting friendships."""
    user_id: int = Field(gt=0, description="ID of user initiating friendship")
    friend_id: int = Field(gt=0, description="ID of friend")
    status: str = Field(default="pending", description="Friendship status")
    created_at: Optional[datetime] = None
    
    @field_validator('status', mode='after')
    @classmethod
    def validate_status(cls, v):
        """Validate friendship status."""
        allowed = {'pending', 'accepted', 'blocked'}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}, got {v}")
        return v
    
    @field_validator('friend_id', mode='after')
    @classmethod
    def validate_friend_not_self(cls, v, info):
        """Ensure user doesn't friend themselves."""
        if 'user_id' in info.data and v == info.data['user_id']:
            raise ValueError("Cannot create friendship with yourself")
        return v


class FriendshipUpdate(BaseModel):
    """Model for updating friendships."""
    status: Optional[str] = None
    
    @field_validator('status', mode='after')
    @classmethod
    def validate_status(cls, v):
        """Validate friendship status."""
        if v is None:
            return v
        allowed = {'pending', 'accepted', 'blocked'}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}, got {v}")
        return v


# ============================================================================
# Role/Permission Models
# ============================================================================

class RoleInsert(BaseModel):
    """Model for inserting roles."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_id": 1,
                "name": "Moderator",
                "color": "#FF0000",
                "permissions": 8,
            }
        }
    )
    
    server_id: int = Field(gt=0, description="ID of server")
    name: str = Field(min_length=1, max_length=50, description="Role name")
    color: Optional[str] = Field(None, pattern=r'^#[0-9A-Fa-f]{6}$', description="Hex color code")
    permissions: int = Field(default=0, ge=0, description="Bitfield of permissions")


class RoleUpdate(BaseModel):
    """Model for updating roles."""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    color: Optional[str] = Field(None, pattern=r'^#[0-9A-Fa-f]{6}$')
    permissions: Optional[int] = Field(None, ge=0)


# ============================================================================
# Emoji/Sticker Models
# ============================================================================

class EmojiInsert(BaseModel):
    """Model for inserting custom emojis."""
    server_id: int = Field(gt=0, description="ID of server (0 for global)")
    name: str = Field(min_length=1, max_length=32, description="Emoji name")
    image_url: str = Field(description="URL to emoji image")
    
    @field_validator('name', mode='after')
    @classmethod
    def validate_emoji_name(cls, v):
        """Emoji names should be alphanumeric with underscores."""
        if not v.replace('_', '').isalnum():
            raise ValueError("Emoji name must be alphanumeric with underscores")
        return v


class EmojiUpdate(BaseModel):
    """Model for updating emojis."""
    name: Optional[str] = Field(None, min_length=1, max_length=32)
    image_url: Optional[str] = None


# ============================================================================
# Audit Log Models
# ============================================================================

class AuditLogInsert(BaseModel):
    """Model for inserting audit log entries."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_id": 1,
                "action": "user_ban",
                "actor_id": 1,
                "target_id": 5,
                "reason": "Spam",
            }
        }
    )
    
    server_id: int = Field(gt=0, description="ID of server")
    action: str = Field(description="Type of action performed")
    actor_id: int = Field(gt=0, description="ID of user who performed action")
    target_id: Optional[int] = None
    changes: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=512)
    created_at: Optional[datetime] = None


# ============================================================================
# Config/Settings Models
# ============================================================================

class UserSettingsInsert(BaseModel):
    """Model for inserting user settings."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": 1,
                "setting_key": "notification_level",
                "setting_value": '"all"',
            }
        }
    )
    
    user_id: int = Field(gt=0, description="ID of user")
    setting_key: str = Field(description="Name of setting")
    setting_value: str = Field(description="JSON-encoded setting value")


class UserSettingsUpdate(BaseModel):
    """Model for updating user settings."""
    setting_value: str = Field(description="JSON-encoded setting value")
