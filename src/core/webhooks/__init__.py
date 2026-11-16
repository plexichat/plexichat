"""
Webhooks module - Zero-friction API for webhook management and execution.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import webhooks
    webhooks.setup(db, auth, messaging, servers, embeds)

    # In any other file (use directly)
    from src.core import webhooks
    webhook = webhooks.create_webhook(user_id=1, channel_id=123, name="My Webhook")
"""

from typing import Optional, List, Dict, Any

import utils.logger as logger

from .models import (
    Webhook,
    WebhookMessage,
    WebhookType,
    WebhookExecution,
)
from .exceptions import (
    WebhookError,
    WebhookNotFoundError,
    WebhookAccessDeniedError,
    InvalidWebhookTokenError,
    WebhookNameError,
    WebhookAvatarError,
    WebhookLimitError,
    ChannelNotFoundError,
    PermissionDeniedError,
    InvalidContentError,
    InvalidWebhookContentError,
    ContentTooLongError,
    RateLimitError,
    EmbedLimitError,
)
from .retry import WebhookRetryQueue
from .delivery import WebhookDeliverer, WebhookDeliveryError, process_due_retries

__all__ = [
    # Models
    "Webhook",
    "WebhookMessage",
    "WebhookType",
    "WebhookExecution",
    # Exceptions
    "WebhookError",
    "WebhookNotFoundError",
    "WebhookAccessDeniedError",
    "InvalidWebhookTokenError",
    "WebhookNameError",
    "WebhookAvatarError",
    "WebhookLimitError",
    "ChannelNotFoundError",
    "PermissionDeniedError",
    "InvalidContentError",
    "InvalidWebhookContentError",
    "ContentTooLongError",
    "RateLimitError",
    "EmbedLimitError",
    # Setup
    "setup",
    # Webhook management
    "create_webhook",
    "get_webhook",
    "get_webhook_by_token",
    "get_channel_webhooks",
    "get_server_webhooks",
    "update_webhook",
    "delete_webhook",
    "regenerate_token",
    # Webhook execution
    "execute_webhook",
    "execute_webhook_by_url",
    # Payload signing / verification
    "sign_payload",
    "verify_payload",
    # Outbound delivery + retries
    "WebhookRetryQueue",
    "WebhookDeliverer",
    "WebhookDeliveryError",
    "process_due_retries",
]

_manager = None
_setup_complete = False


def setup(
    db: Any,
    auth_module: Optional[Any] = None,
    messaging_module: Optional[Any] = None,
    servers_module: Optional[Any] = None,
    embeds_module: Optional[Any] = None,
) -> None:
    """
    Initialize the webhooks module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module for token utilities
        messaging_module: Optional messaging module for sending messages
        servers_module: Optional servers module for permission checks
        embeds_module: Optional embeds module for rich embeds
    """
    global _manager, _setup_complete

    from .manager import WebhookManager

    _manager = WebhookManager(
        db, auth_module, messaging_module, servers_module, embeds_module
    )
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Webhooks module not initialized. Call webhooks.setup(db) first."
        )
    return _manager


# === Webhook Management ===


def create_webhook(
    user_id: int,
    channel_id: int,
    name: str,
    avatar_url: Optional[str] = None,
    require_signed_deliveries: bool = False,
) -> Webhook:
    """Create a new webhook for a channel."""
    return _get_manager().create_webhook(
        user_id, channel_id, name, avatar_url, require_signed_deliveries
    )


def get_webhook(webhook_id: int, user_id: Optional[int] = None) -> Optional[Webhook]:
    """Get a webhook by ID."""
    return _get_manager().get_webhook(webhook_id, user_id)


def get_webhook_by_token(token: str) -> Optional[Webhook]:
    """Get a webhook by its token (for execution)."""
    return _get_manager().get_webhook_by_token(token)


def get_channel_webhooks(user_id: int, channel_id: int) -> List[Webhook]:
    """Get all webhooks for a channel."""
    return _get_manager().get_channel_webhooks(user_id, channel_id)


def get_server_webhooks(user_id: int, server_id: int) -> List[Webhook]:
    """Get all webhooks for a server."""
    return _get_manager().get_server_webhooks(user_id, server_id)


def update_webhook(
    user_id: int,
    webhook_id: int,
    name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    channel_id: Optional[int] = None,
    require_signed_deliveries: Optional[bool] = None,
) -> Webhook:
    """Update a webhook."""
    return _get_manager().update_webhook(
        user_id, webhook_id, name, avatar_url, channel_id, require_signed_deliveries
    )


def delete_webhook(user_id: int, webhook_id: int) -> bool:
    """Delete a webhook."""
    return _get_manager().delete_webhook(user_id, webhook_id)


def regenerate_token(user_id: int, webhook_id: int) -> Webhook:
    """Regenerate a webhook's token."""
    return _get_manager().regenerate_token(user_id, webhook_id)


# === Webhook Execution ===


def execute_webhook(
    webhook_id: int,
    token: str,
    content: Optional[str] = None,
    username: Optional[str] = None,
    avatar_url: Optional[str] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    thread_id: Optional[int] = None,
    wait: bool = True,
) -> Optional[WebhookMessage]:
    """Execute a webhook to send a message."""
    return _get_manager().execute_webhook(
        webhook_id, token, content, username, avatar_url, embeds, thread_id, wait
    )


def execute_webhook_by_url(
    webhook_url: str,
    content: Optional[str] = None,
    username: Optional[str] = None,
    avatar_url: Optional[str] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    thread_id: Optional[int] = None,
    wait: bool = True,
) -> Optional[WebhookMessage]:
    """Execute a webhook using its URL."""
    return _get_manager().execute_webhook_by_url(
        webhook_url, content, username, avatar_url, embeds, thread_id, wait
    )


# === Payload signing / verification ===


def sign_payload(webhook_id: int, payload: Dict[str, Any]) -> Dict[str, str]:
    """Sign an outbound payload with a webhook's Ed25519 key.

    Exposed at module level so the API layer (which talks to this module, not
    to the private manager singleton) can produce verifiable deliveries.
    """
    return _get_manager().sign_payload(webhook_id, payload)


def verify_payload(
    webhook_id: int,
    payload: Dict[str, Any],
    signature_header: Optional[str],
    timestamp_header: Optional[str],
    now_ms: Optional[int] = None,
) -> bool:
    """Verify a signed inbound delivery, failing CLOSED on any error.

    The manager performs the Ed25519 check (constant-time in the cryptography
    backend) and enforces the replay cache. This wrapper guarantees that an
    unavailable or partially configured manager can never be mistaken for a
    valid signature: every error path returns ``False`` so the caller rejects
    the delivery instead of executing it unverified.
    """
    try:
        return bool(
            _get_manager().verify_payload(
                webhook_id, payload, signature_header, timestamp_header, now_ms
            )
        )
    except Exception as exc:
        logger.error(f"Webhook signature verification unavailable for {webhook_id}: {exc}")
        return False
