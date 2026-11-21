from typing import Any, Dict, Optional

import secrets
import time

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager
from src.core.security.hardening import bounded_int

from ..validation import validate_config_url_pattern
from ..oauth import OAuth2Flow
from ..commands import CommandRegistry
from ..interactions import InteractionHandler
from .application_crud import AppCRUDMixin
from .installations import InstallationMixin
from .bot_management import BotManagementMixin
from .admin import AdminMixin
from .webhook import WebhookMixin
from .ratelimit import RatelimitMixin


# A configured webhook signing secret must carry at least the strength of the
# secrets the system generates for this purpose (``secrets.token_urlsafe(48)``
# here, ``secrets.token_hex(32)`` in config_defaults/startup: 64 urlsafe/hex
# chars). Every other credential-setting path validates a caller-supplied value
# before it becomes a bearer/signing secret; without this floor a short
# operator value becomes the HMAC key for webhook delivery signatures and can be
# brute-forced from a single captured delivery. Treat an under-strength value
# exactly like the legacy literal: never use it, rotate to a fresh generated one.
MIN_WEBHOOK_SECRET_LENGTH = 32


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
            # Deployments can require PKCE for every authorization code.
            # The default preserves legacy confidential-client flows; once a
            # challenge is bound to a code it is ALWAYS enforced at exchange.
            "pkce_enabled": self._config.get("oauth", {}).get("pkce_enabled", False),
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

        oauth_config = dict(merged.get("oauth") or {})
        oauth_config["allowed_redirect_uri_pattern"] = validate_config_url_pattern(
            oauth_config.get("allowed_redirect_uri_pattern", "^https?://")
        )
        merged["oauth"] = oauth_config
        merged["webhook_signature_max_skew_seconds"] = bounded_int(
            merged.get("webhook_signature_max_skew_seconds", 300),
            "webhook_signature_max_skew_seconds",
            minimum=0,
            maximum=86_400,
        )
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
        if (
            configured
            and configured != legacy_default
            and len(configured) >= MIN_WEBHOOK_SECRET_LENGTH
        ):
            return configured
        if configured and configured != legacy_default:
            logger.warning(
                "Configured webhook_signature_secret is below the "
                f"{MIN_WEBHOOK_SECRET_LENGTH}-character minimum; rotating to a "
                "fresh generated secret"
            )

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
