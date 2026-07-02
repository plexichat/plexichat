"""
Webhook name and avatar URL validation.
"""

import re
from typing import Optional
from urllib.parse import urlparse

import utils.logger as logger
from src.core.webhooks.exceptions import WebhookAvatarError, WebhookNameError

from .base import WebhookManagerTrait
from .constants import WEBHOOK_NAME_MAX_LENGTH


class ValidationMixin(WebhookManagerTrait):
    """Webhook name and avatar URL validation."""

    def _validate_name(self, name: str) -> str:
        """Validate and sanitize webhook name."""
        if not name or not name.strip():
            raise WebhookNameError("Webhook name cannot be empty")

        name = name.strip()

        if len(name) > WEBHOOK_NAME_MAX_LENGTH:
            raise WebhookNameError(
                f"Webhook name cannot exceed {WEBHOOK_NAME_MAX_LENGTH} characters",
                WEBHOOK_NAME_MAX_LENGTH,
            )

        name = re.sub(r"<[^>]*>", "", name)
        name = re.sub(r"javascript:", "", name, flags=re.IGNORECASE)

        name = name.strip()
        if not name:
            raise WebhookNameError(
                "Webhook name contains only restricted characters or becomes empty after sanitization"
            )

        return name

    def _validate_avatar_url(self, url: Optional[str]) -> Optional[str]:
        """Validate avatar URL and prevent SSRF."""
        if not url:
            return None

        url = url.strip()
        if not url:
            return None

        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise WebhookAvatarError("Avatar URL must use http or https")
            if not parsed.netloc:
                raise WebhookAvatarError("Invalid avatar URL")
        except Exception as e:
            if isinstance(e, WebhookAvatarError):
                raise
            raise WebhookAvatarError(f"Invalid avatar URL: {str(e)}")

        if "javascript:" in url.lower() or "data:" in url.lower():
            raise WebhookAvatarError("Invalid avatar URL scheme")

        try:
            from src.utils.security import URLValidator

            validator = URLValidator()
            validator.validate_url_for_request(url)
        except ImportError:
            logger.warning("URLValidator not available for SSRF protection")
        except ValueError as e:
            logger.warning(f"Blocked unsafe webhook avatar URL: {url} - {e}")
            raise WebhookAvatarError(f"Unsafe avatar URL: {str(e)}")

        return url
