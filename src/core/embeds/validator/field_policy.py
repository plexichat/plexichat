"""Length and active-content policy for embed color/provider fields."""

from __future__ import annotations

from typing import Any, Dict

from .active_content import deny_active_content
from .color import validate_color
from .content import sanitize_content
from .ssrf_gate import validate_embed_url
from ..exceptions import EmbedValidationError, InvalidColorError

MAX_PROVIDER_NAME_LENGTH = 256
MAX_COLOR_FIELD_LENGTH = 32


def validate_color_field(value: Any, field: str = "color") -> str:
    """Validate a color string after rejecting CSS/script injection sinks."""
    text = deny_active_content(value, field, InvalidColorError)
    if len(text) > MAX_COLOR_FIELD_LENGTH:
        raise InvalidColorError(f"{field} exceeds maximum length", text)
    return validate_color(text)


def validate_provider_fields(provider: Any, path: str = "provider") -> Dict[str, Any]:
    """Validate and sanitize the ``provider`` sub-object of an embed.

    Provider names are rendered as labels and provider URLs become links, so
    both are bounded and passed through the active-content denylist / URL gate
    before they reach storage.
    """
    if not isinstance(provider, dict):
        raise EmbedValidationError(f"{path} must be an object", [path])

    sanitized: Dict[str, Any] = {}
    name = provider.get("name")
    if name is not None:
        text = deny_active_content(name, f"{path}.name")
        text = sanitize_content(text, f"{path}.name")
        if len(text) > MAX_PROVIDER_NAME_LENGTH:
            raise EmbedValidationError(
                f"{path}.name exceeds maximum length of {MAX_PROVIDER_NAME_LENGTH}",
                [f"{path}.name"],
            )
        sanitized["name"] = text

    url = provider.get("url")
    if url is not None:
        sanitized["url"] = validate_embed_url(url, f"{path}.url", allow_relative=True)

    return sanitized
