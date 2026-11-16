"""
Message embed sanitization.

Recursive validation of message embed dicts accepted by the message-send
API. Stored verbatim and rendered by the client, so every string field is
sanitized and URL-typed keys are validated for shape and SSRF.
"""

from typing import Any, Dict, List, Optional

from .bounds import (
    MAX_EMBED_NESTING_DEPTH,
    MAX_EMBED_KEYS_PER_OBJECT,
    MAX_EMBED_LIST_ITEMS,
    EmbedBudget,
    field_string_limit,
)
from .color import validate_color
from .content import sanitize_content
from .constants import (
    FIELD_NAME_MAX_LENGTH,
    FIELD_VALUE_MAX_LENGTH,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_FIELDS,
)
from .schema import _coerce_dimension
from .ssrf_gate import validate_embed_url
from .length import validate_string_length
from .timestamp import validate_timestamp
from .url_validation import validate_url
from ..exceptions import (
    EmbedValidationError,
    InvalidColorError,
)

_MESSAGE_EMBED_URL_KEYS = {"url", "icon_url"}
_MESSAGE_EMBED_DIMENSION_KEYS = {"width", "height"}

# Defence-in-depth: the embed dict is ``Dict[str, Any]`` and the sanitizer
# recurses into any nested dict/list. Nothing in the schema bounds nesting
# depth, so a small, deeply-nested payload could exhaust the interpreter stack
# (RecursionError -> 500) before any field limit applies. Cap the recursion
# explicitly and fail closed with a normal validation error.
# Depth constant now lives in ``bounds``.


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
            ``MAX_EMBEDS_PER_MESSAGE``, nests deeper than
            ``MAX_EMBED_NESTING_DEPTH``, or any embed contains unsafe
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
    # One budget is shared by every embed in the message so a large batch of
    # individually-legal embeds cannot collectively exhaust the process.
    budget = EmbedBudget()
    return [
        _sanitize_message_embed(embed, f"embeds[{i}]", depth=0, budget=budget)
        for i, embed in enumerate(embeds)
    ]


def _sanitize_message_embed(
    embed: Any, path: str, depth: int = 0, budget: EmbedBudget | None = None
) -> Dict[str, Any]:
    """Recursively sanitize a single message embed dict."""
    if budget is None:
        budget = EmbedBudget()
    if depth > MAX_EMBED_NESTING_DEPTH:
        raise EmbedValidationError(
            f"{path} exceeds maximum embed nesting depth ({MAX_EMBED_NESTING_DEPTH})",
            [path],
        )
    if not isinstance(embed, dict):
        raise EmbedValidationError(f"{path} must be an object", [path])

    budget.charge_node(path)
    if len(embed) > MAX_EMBED_KEYS_PER_OBJECT:
        raise EmbedValidationError(
            f"{path} has too many keys ({len(embed)}/{MAX_EMBED_KEYS_PER_OBJECT})",
            [path],
        )

    sanitized: Dict[str, Any] = {}
    for key, value in embed.items():
        child_path = f"{path}.{key}"
        budget.charge_node(child_path)
        if value is None:
            sanitized[key] = None
            continue
        if key in _MESSAGE_EMBED_URL_KEYS:
            validated = validate_embed_url(str(value), child_path, allow_relative=True)
            sanitize_content(str(value), child_path)
            budget.charge_text(value, child_path)
            sanitized[key] = validated
        elif key == "color":
            sanitized[key] = _validate_message_color(value, child_path)
        elif key == "timestamp":
            budget.charge_text(value, child_path)
            sanitized[key] = validate_timestamp(str(value))
        elif key in _MESSAGE_EMBED_DIMENSION_KEYS:
            issues: List[str] = []
            n = _coerce_dimension(value, child_path, issues)
            if issues:
                raise EmbedValidationError(issues[0], [child_path])
            sanitized[key] = n
        elif key == "fields":
            sanitized[key] = _sanitize_message_fields(value, child_path, budget)
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_message_embed(value, child_path, depth=depth + 1, budget=budget)
        elif isinstance(value, list):
            if len(value) > MAX_EMBED_LIST_ITEMS:
                raise EmbedValidationError(
                    f"{child_path} has too many items ({len(value)}/{MAX_EMBED_LIST_ITEMS})",
                    [child_path],
                )
            items: List[Any] = []
            for j, v in enumerate(value):
                item_path = f"{child_path}[{j}]"
                budget.charge_node(item_path)
                if isinstance(v, dict):
                    items.append(_sanitize_message_embed(v, item_path, depth=depth + 1, budget=budget))
                elif isinstance(v, str):
                    budget.charge_text(v, item_path, field_string_limit(key))
                    items.append(sanitize_content(v, item_path))
                else:
                    items.append(v)
            sanitized[key] = items
        elif isinstance(value, str):
            budget.charge_text(value, child_path, field_string_limit(key))
            sanitized[key] = sanitize_content(value, child_path)
        else:
            sanitized[key] = value
    return sanitized

def _sanitize_message_fields(
    fields: Any, path: str, budget: EmbedBudget | None = None
) -> List[Dict[str, Any]]:
    """Validate and sanitize an embed's ``fields`` list."""
    if budget is None:
        budget = EmbedBudget()
    if not isinstance(fields, list):
        raise EmbedValidationError(f"{path} must be a list", [path])
    if len(fields) > MAX_FIELDS:
        raise EmbedValidationError(
            f"{path} cannot have more than {MAX_FIELDS} fields",
            [f"{path}: {len(fields)}/{MAX_FIELDS}"],
        )

    result: List[Dict[str, Any]] = []
    for j, field in enumerate(fields):
        field_path = f"{path}[{j}]"
        budget.charge_node(field_path)
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
        budget.charge_text(name, f"{field_path}.name", FIELD_NAME_MAX_LENGTH)
        budget.charge_text(value, f"{field_path}.value", FIELD_VALUE_MAX_LENGTH)
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
