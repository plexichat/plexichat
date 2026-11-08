"""
Message embed sanitization.

Recursive validation of message embed dicts accepted by the message-send
API. Stored verbatim and rendered by the client, so every string field is
sanitized and URL-typed keys are validated for shape and SSRF.
"""

from typing import Any, Dict, List, Optional

from .color import validate_color
from .content import sanitize_content
from .constants import (
    FIELD_NAME_MAX_LENGTH,
    FIELD_VALUE_MAX_LENGTH,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_FIELDS,
)
from .schema import _coerce_dimension
from .length import validate_string_length
from .timestamp import validate_timestamp
from .url_validation import validate_url
from ..exceptions import (
    EmbedValidationError,
    InvalidColorError,
)

_MESSAGE_EMBED_URL_KEYS = {"url", "icon_url"}
_MESSAGE_EMBED_DIMENSION_KEYS = {"width", "height"}


def sanitize_message_embeds(
    embeds: Optional[List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Validate and sanitize a list of embeds for message storage.

    The message-send API accepts raw embed dicts that are stored verbatim
    and later rendered by the client. Without server-side sanitization a
    malicious embed becomes stored XSS.

    Args:
        embeds: List of raw embed dicts from the message request body.

    Returns:
        Sanitized copy of the embed list (``None`` if input was ``None``).

    Raises:
        EmbedValidationError: If the list is malformed, exceeds
            ``MAX_EMBEDS_PER_MESSAGE``, or any embed contains unsafe
            content.
    """
    if not embeds:
        return embeds
    if not isinstance(embeds, list):
        raise EmbedValidationError("embeds must be a list", ["embeds"])
    if len(embeds) > MAX_EMBEDS_PER_MESSAGE:
        raise EmbedValidationError(
            f"Message cannot have more than {MAX_EMBEDS_PER_MESSAGE} embeds",
            [f"embeds: {len(embeds)}/{MAX_EMBEDS_PER_MESSAGE}"],
        )
    return [
        _sanitize_message_embed(embed, f"embeds[{i}]") for i, embed in enumerate(embeds)
    ]


def _sanitize_message_embed(embed: Any, path: str) -> Dict[str, Any]:
    """Recursively sanitize a single message embed dict."""
    if not isinstance(embed, dict):
        raise EmbedValidationError(f"{path} must be an object", [path])

    sanitized: Dict[str, Any] = {}
    for key, value in embed.items():
        if value is None:
            sanitized[key] = None
            continue
        if key in _MESSAGE_EMBED_URL_KEYS:
            validated = validate_url(str(value), f"{path}.{key}")
            # Shape-valid https URLs can still carry XSS payloads in their
            # path/query (e.g. an onerror= fragment for a client attribute
            # sink): apply the dangerous-pattern blocklist to URL keys too.
            sanitize_content(str(value), f"{path}.{key}")
            sanitized[key] = validated
        elif key == "color":
            sanitized[key] = _validate_message_color(value, f"{path}.color")
        elif key == "timestamp":
            sanitized[key] = validate_timestamp(str(value))
        elif key in _MESSAGE_EMBED_DIMENSION_KEYS:
            issues: List[str] = []
            n = _coerce_dimension(value, f"{path}.{key}", issues)
            if issues:
                raise EmbedValidationError(issues[0], [f"{path}.{key}"])
            sanitized[key] = n
        elif key == "fields":
            sanitized[key] = _sanitize_message_fields(value, f"{path}.fields")
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_message_embed(value, f"{path}.{key}")
        elif isinstance(value, list):
            sanitized[key] = [
                _sanitize_message_embed(v, f"{path}.{key}[{j}]")
                if isinstance(v, dict)
                else (
                    sanitize_content(str(v), f"{path}.{key}[{j}]")
                    if isinstance(v, str)
                    else v
                )
                for j, v in enumerate(value)
            ]
        elif isinstance(value, str):
            sanitized[key] = sanitize_content(value, f"{path}.{key}")
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_message_fields(fields: Any, path: str) -> List[Dict[str, Any]]:
    """Validate and sanitize an embed's ``fields`` list."""
    if not isinstance(fields, list):
        raise EmbedValidationError(f"{path} must be a list", [path])
    if len(fields) > MAX_FIELDS:
        raise EmbedValidationError(
            f"{path} cannot have more than {MAX_FIELDS} fields",
            [f"{path}: {len(fields)}/{MAX_FIELDS}"],
        )

    result: List[Dict[str, Any]] = []
    for j, field in enumerate(fields):
        if not isinstance(field, dict):
            raise EmbedValidationError(
                f"{path}[{j}] must be an object", [f"{path}[{j}]"]
            )
        name = field.get("name")
        value = field.get("value")
        if name is None:
            raise EmbedValidationError(
                f"{path}[{j}].name is required", [f"{path}[{j}].name"]
            )
        if value is None:
            raise EmbedValidationError(
                f"{path}[{j}].value is required", [f"{path}[{j}].value"]
            )
        result.append(
            {
                "name": validate_string_length(
                    sanitize_content(str(name), f"{path}[{j}].name"),
                    FIELD_NAME_MAX_LENGTH,
                    f"{path}[{j}].name",
                ),
                "value": validate_string_length(
                    sanitize_content(str(value), f"{path}[{j}].value"),
                    FIELD_VALUE_MAX_LENGTH,
                    f"{path}[{j}].value",
                ),
                "inline": bool(field.get("inline", False)),
            }
        )
    return result


def _validate_message_color(value: Any, path: str) -> str:
    """Validate an embed color that may be an int or hex string."""
    if isinstance(value, bool):
        raise InvalidColorError(f"{path} must be a valid hex color", str(value))
    if isinstance(value, int):
        if value < 0 or value > 0xFFFFFF:
            raise InvalidColorError(f"{path} must be a valid hex color", str(value))
        return f"#{value:06X}"
    return validate_color(str(value))
