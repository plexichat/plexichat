"""
Webhook permission checking helpers.
"""

from typing import Optional

from src.core.base import SnowflakeID

from .base import WebhookManagerTrait


class PermissionMixin(WebhookManagerTrait):
    """Permission checking helpers."""

    def _check_manage_webhooks_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if user has manage_webhooks permission."""
        if not self._servers:
            return True
        return self._servers.has_permission(
            user_id, server_id, "webhooks.manage", channel_id
        )
