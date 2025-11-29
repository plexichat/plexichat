"""
User schemas - Request/response models for user endpoints.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


class UserUpdateRequest(BaseModel):
    """User update request."""
    model_config = ConfigDict(from_attributes=True)
    
    username: Optional[str] = Field(None, min_length=3, max_length=32, description="New username")
    email: Optional[str] = Field(None, description="New email")
    avatar_url: Optional[str] = Field(None, description="New avatar URL")
    password: Optional[str] = Field(None, min_length=8, description="New password")
    current_password: Optional[str] = Field(None, description="Current password (required for password change)")


class UserPublicResponse(BaseModel):
    """Public user information response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    avatar_url: Optional[str] = Field(None, description="Avatar URL")
    created_at: int = Field(..., description="Account creation timestamp")
    
    @field_serializer("id")
    def serialize_id(self, v: Any) -> Optional[str]:
        return str(v) if v else None
