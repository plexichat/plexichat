"""Base class for ServersManager."""

from typing import Any, Dict, Optional

import secrets

import utils.config as config

from src.core.base import BaseManager


class ServersManagerBase(BaseManager):
    """Base class for ServersManager.

    Holds instances of the four underlying managers:
    - ServerManager: Core server, channel, role, member operations
    - ScheduledEventManager: Scheduled event operations
    - TemplateManager: Template operations
    - OnboardingManager: Onboarding and welcome screen operations
    """

    _manager: Any = None
    _event_manager: Any = None
    _template_manager: Any = None
    _onboarding_manager: Any = None

    _config: Optional[Dict[str, Any]] = None
    instance_id: str = ""

    def __init__(
        self,
        db: Any,
        auth_module: Optional[Any] = None,
        messaging_module: Optional[Any] = None,
        notifications_module: Optional[Any] = None,
        events_module: Optional[Any] = None,
    ) -> None:
        """Initialize ServersManager with all sub-managers.

        Args:
            db: Database instance
            auth_module: Optional auth module for user verification
            messaging_module: Optional messaging module for channel messages
            notifications_module: Optional notifications module for event reminders
            events_module: Optional events module for dispatching
        """
        super().__init__(db, auth_module)

        from .manager.base import ServerManager
        from .events import ScheduledEventManager
        from .templates import TemplateManager
        from .onboarding import OnboardingManager

        self._manager = ServerManager(db, auth_module, messaging_module)
        self._event_manager = ScheduledEventManager(
            db, self._manager, notifications_module, events_module
        )
        self._template_manager = TemplateManager(db, self._manager)
        self._onboarding_manager = OnboardingManager(db, self._manager)

        self._config = self._load_config()
        self._encrypt_descriptions = config.get(
            "encryption.encrypt_descriptions", False
        )
        self._encrypt_thread_names = config.get(
            "encryption.encrypt_thread_names", False
        )
        self.instance_id = secrets.token_hex(4)

        self._cache_ttl = 60
        self._member_cache_prefix = "srv_member:"
        self._permission_cache_prefix = "srv_permission:"
        self._channel_cache_prefix = "srv_channel:"
        self._server_owner_cache_prefix = "srv_owner:"
        self._member_roles_cache_prefix = "srv_member_roles:"

    def _load_config(self) -> Dict[str, Any]:
        """Load server configuration."""
        defaults = {
            "max_servers_per_user": 100,
            "max_channels_per_server": 500,
            "max_roles_per_server": 250,
            "max_members_per_server": 250000,
            "server_name_min_length": 2,
            "server_name_max_length": 100,
            "channel_name_min_length": 1,
            "channel_name_max_length": 100,
            "role_name_min_length": 1,
            "role_name_max_length": 100,
            "invite_code_length": 8,
        }

        server_config = config.get("servers", {})
        return {**defaults, **server_config}

    def _log_audit(
        self,
        server_id: Any,
        user_id: Any,
        action: Any,
        target_type: Optional[str] = None,
        target_id: Optional[Any] = None,
        changes: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Log an audit entry."""
        self._manager._log_audit(
            server_id, user_id, action, target_type, target_id, changes, reason
        )
