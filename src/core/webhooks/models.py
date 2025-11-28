"""
Webhook models - Dataclasses for all webhook-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class WebhookType(Enum):
    """Types of webhooks."""
    INCOMING = "incoming"
    CHANNEL_FOLLOWER = "channel_follower"


@dataclass
class Webhook:
    """Represents a webhook for a channel."""
    id: int
    channel_id: int
    server_id: int
    creator_id: int
    name: str
    webhook_type: WebhookType = WebhookType.INCOMING
    avatar_url: Optional[str] = None
    token: Optional[str] = None
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
    id: int
    webhook_id: int
    channel_id: int
    content: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    embeds: List[Dict[str, Any]] = field(default_factory=list)
    thread_id: Optional[int] = None
    created_at: int = 0


@dataclass
class WebhookExecution:
    """Result of a webhook execution."""
    success: bool
    message: Optional[WebhookMessage] = None
    error: Optional[str] = None
