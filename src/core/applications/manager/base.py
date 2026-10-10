from typing import Any, Dict, Optional

import secrets
import time

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
            # NOTE: no default webhook secret. See _resolve_webhook_secret.
            "webhook_signature_max_skew_seconds": 300,
        }

        app_config = config.get("applications", {})
        merged = {**defaults, **app_config}
        merged["webhook_signature_secret"] = self._resolve_webhook_secret(app_config)
        return merged

    @staticmethod
    def _resolve_webhook_secret(app_config: Dict[str, Any]) -> str:
        """Return a per-installation webhook signing secret.

        SECURITY: deployments must never share a static signing secret, so
        a random secret is generated once and persisted to the config
        store. The legacy hard-coded ``plexichat-webhook-secret`` value is
        treated as unset and rotated automatically on first boot.

        Persistence is retried with a short backoff; if it still fails the
        startup HARD-FAILS. Continuing with an unpersisted secret would give
        every process a different in-memory signing secret (divergent
        verification results depending on which worker handles a webhook).
        """
        configured = app_config.get("webhook_signature_secret") or ""
        legacy_default = "plexichat-webhook-secret"
        if configured and configured != legacy_default:
            return configured

        generated = secrets.token_urlsafe(48)
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                applications_cfg = dict(config.get("applications", {}) or {})
                applications_cfg["webhook_signature_secret"] = generated
                config.set("applications", applications_cfg)
                logger.info("Generated new per-installation webhook signing secret")
                return generated
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"Could not persist webhook signing secret to config "
                    f"(attempt {attempt}/3): {exc}"
                )
                if attempt < 3:
                    time.sleep(0.2 * attempt)
        raise RuntimeError(
            "Failed to persist the generated webhook signing secret after "
            f"3 attempts: {last_exc}. Refusing to start: processes would run "
            "with divergent in-memory secrets and webhook signature "
            "verification would be inconsistent."
        )
