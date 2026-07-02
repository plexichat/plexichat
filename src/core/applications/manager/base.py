from typing import Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager

from ..oauth import OAuth2Flow
from ..commands import CommandRegistry
from ..interactions import InteractionHandler
from .application_crud import AppCRUDMixin
from .installations import InstallationMixin
from .bot_management import BotManagementMixin
from .admin import AdminMixin
from .webhook import WebhookMixin
from .ratelimit import RatelimitMixin


class ApplicationManager(
    BaseManager,
    AppCRUDMixin,
    InstallationMixin,
    BotManagementMixin,
    AdminMixin,
    WebhookMixin,
    RatelimitMixin,
):
    def __init__(self, db, auth_module=None, servers_module=None, events_module=None):
        super().__init__(db, auth_module)
        self._servers = servers_module
        self._events = events_module
        self._config = self._load_config()

        oauth_config = {
            "token_expiry_seconds": self._config.get("oauth", {}).get(
                "token_expiry_seconds", 604800
            ),
            "code_expiry_seconds": self._config.get("oauth", {}).get(
                "code_expiry_seconds", 600
            ),
            "refresh_enabled": self._config.get("oauth", {}).get(
                "refresh_enabled", True
            ),
            "authorization_endpoint": "/oauth2/authorize",
        }
        self._oauth = OAuth2Flow(db, oauth_config)

        command_config = {
            "max_commands_per_app": self._config.get("command_limits", {}).get(
                "max_commands_per_app", 100
            ),
            "max_options_per_command": self._config.get("command_limits", {}).get(
                "max_options_per_command", 25
            ),
        }
        self._commands = CommandRegistry(db, command_config)

        interaction_config = {
            "interaction_timeout": self._config.get("interaction_timeout", 900),
        }
        self._interactions = InteractionHandler(db, interaction_config, events_module)

        self._rate_limits = {}

        logger.info("Application module initialized")

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "max_applications_per_user": 25,
            "oauth": {
                "token_expiry_seconds": 604800,
                "code_expiry_seconds": 600,
                "refresh_enabled": True,
                "allowed_redirect_uri_pattern": "^https?://",
            },
            "command_limits": {
                "max_commands_per_app": 100,
                "max_options_per_command": 25,
            },
            "interaction_timeout": 900,
            "rate_limits": {
                "requests_per_minute": 50,
                "burst_limit": 10,
            },
            "webhook_signature_secret": "plexichat-webhook-secret",  # pragma: allowlist secret
        }

        app_config = config.get("applications", {})
        return {**defaults, **app_config}
