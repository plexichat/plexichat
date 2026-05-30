"""
Webhook token generation, hashing, and verification.
"""

from .base import WebhookManagerTrait


class TokenMixin(WebhookManagerTrait):
    """Token generation, hashing, verification, and formatting."""

    pass
