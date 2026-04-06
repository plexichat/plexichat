"""
Webhook models - Dataclasses for all webhook-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from src.core.base import SnowflakeID


class WebhookType(Enum):
    """Types of webhooks."""

    INCOMING = "incoming"
    CHANNEL_FOLLOWER = "channel_follower"


@dataclass
class Webhook:
    """Represents a webhook for a channel."""

    id: SnowflakeID
    channel_id: SnowflakeID
    server_id: SnowflakeID
    creator_id: SnowflakeID
    name: str
    webhook_type: WebhookType = WebhookType.INCOMING
    avatar_url: Optional[str] = None
    token: Optional[str] = None
    # Ed25519 signing keys for webhook request verification
    signing_key_public: Optional[bytes] = None  # 32-byte public key for signature verification
    signing_key_private_encrypted: Optional[str] = None  # Encrypted private key (base64)
    created_at: int = 0
    updated_at: int = 0

    @property
    def url(self) -> str:
        """Get the webhook URL."""
        if self.token:
            return f"/webhooks/{self.id}/{self.token}"
        return f"/webhooks/{self.id}"


@dataclass
class WebhookMessage:
    """Represents a message sent via webhook."""

    id: SnowflakeID
    webhook_id: SnowflakeID
    channel_id: SnowflakeID
    content: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    embeds: List[Dict[str, Any]] = field(default_factory=list)
    thread_id: Optional[SnowflakeID] = None
    created_at: int = 0


@dataclass
class WebhookExecution:
    """Result of a webhook execution."""

    success: bool
    message: Optional[WebhookMessage] = None
    error: Optional[str] = None
