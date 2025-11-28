"""
API schemas - Pydantic models for request/response validation.
"""

from .common import (
    SnowflakeID,
    ErrorResponse,
    ErrorDetail,
    PaginationParams,
    PaginatedResponse,
)
from .auth import (
    RegisterRequest,
    LoginRequest,
    LoginResponse,
    TwoFactorRequest,
    TokenResponse,
    UserResponse,
    SessionResponse,
)
from .users import (
    UserUpdateRequest,
    UserPublicResponse,
)
from .servers import (
    ServerCreateRequest,
    ServerUpdateRequest,
    ServerResponse,
    ChannelResponse,
    ChannelUpdateRequest,
    RoleResponse,
    MemberResponse,
    InviteResponse,
)
from .channels import (
    ChannelCreateRequest,
)
from .messages import (
    MessageCreateRequest,
    MessageUpdateRequest,
    MessageResponse,
    AttachmentResponse,
)

__all__ = [
    # Common
    "SnowflakeID",
    "ErrorResponse",
    "ErrorDetail",
    "PaginationParams",
    "PaginatedResponse",
    # Auth
    "RegisterRequest",
    "LoginRequest",
    "LoginResponse",
    "TwoFactorRequest",
    "TokenResponse",
    "UserResponse",
    "SessionResponse",
    # Users
    "UserUpdateRequest",
    "UserPublicResponse",
    # Servers
    "ServerCreateRequest",
    "ServerUpdateRequest",
    "ServerResponse",
    "ChannelResponse",
    "ChannelUpdateRequest",
    "RoleResponse",
    "MemberResponse",
    "InviteResponse",
    # Channels
    "ChannelCreateRequest",
    # Messages
    "MessageCreateRequest",
    "MessageUpdateRequest",
    "MessageResponse",
    "AttachmentResponse",
]
