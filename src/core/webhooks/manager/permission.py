"""
Webhook permission checking helpers.
"""

from typing import Optional

import utils.logger as logger
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
        """Check if user has manage_webhooks permission.

        SECURITY: fail CLOSED when the servers module is absent. Without
        it we cannot evaluate ``webhooks.manage``, so deny instead of
        granting every authenticated user manage rights over every
        server/channel.
        """
        if not self._servers:
            logger.error(
                "Webhooks permission check failed CLOSED: servers module "
                "is not wired into the webhooks manager. Denying request."
            )
            return False
        return self._servers.has_permission(
            user_id, server_id, "webhooks.manage", channel_id
        )
