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
from .polls import (
    PollInlineCreateRequest,
    PollCreateRequest,
    PollVoteRequest,
    PollOptionResponse,
    PollResponse,
    PollResultsResponse,
)
from .relationships import (
    FriendRequestCreate,
    BlockCreate,
    RelationshipResponse,
    FriendRequestResponse,
)
from .presence import (
    PresenceUpdate,
    PresenceResponse,
    ActivityResponse,
)
from .reactions import (
    ReactionResponse,
    ReactionUserResponse,
    MessageReactionsResponse,
)
from .webhooks import (
    WebhookCreateRequest,
    WebhookUpdateRequest,
    WebhookResponse,
    WebhookExecuteRequest,
    WebhookMessageResponse,
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
    # Polls
    "PollInlineCreateRequest",
    "PollCreateRequest",
    "PollVoteRequest",
    "PollOptionResponse",
    "PollResponse",
    "PollResultsResponse",
    # Relationships
    "FriendRequestCreate",
    "BlockCreate",
    "RelationshipResponse",
    "FriendRequestResponse",
    # Presence
    "PresenceUpdate",
    "PresenceResponse",
    "ActivityResponse",
    # Reactions
    "ReactionResponse",
    "ReactionUserResponse",
    "MessageReactionsResponse",
    # Webhooks
    "WebhookCreateRequest",
    "WebhookUpdateRequest",
    "WebhookResponse",
    "WebhookExecuteRequest",
    "WebhookMessageResponse",
]
