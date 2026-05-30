"""
WebhookManager composition - combines all mixins into the final class.
"""

from typing import Any

from .base import WebhookManagerBase
from .token import TokenMixin
from .validation import ValidationMixin
from .permission import PermissionMixin
from .crud import WebhookCRUDMixin
from .execute import WebhookExecutionMixin
from .signature import SignatureMixin


class WebhookManager(
    WebhookManagerBase,
    TokenMixin,
    ValidationMixin,
    PermissionMixin,
    WebhookCRUDMixin,
    WebhookExecutionMixin,
    SignatureMixin,
):
    """Full webhook manager combining all mixins."""

    def __init__(
        self,
        db: Any,
        auth_module: Any = None,
        messaging_module: Any = None,
        servers_module: Any = None,
        embeds_module: Any = None,
    ) -> None:
        """Initialize the webhook manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Auth module for token utilities
            messaging_module: Messaging module for sending messages
            servers_module: Servers module for permission checks
            embeds_module: Embeds module for rich embeds
        """
        WebhookManagerBase.__init__(
            self, db, auth_module, messaging_module, servers_module, embeds_module
        )


__all__ = ["WebhookManager"]
