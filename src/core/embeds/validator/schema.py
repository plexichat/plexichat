"""
Embed schema validation.

Complete embed data validation: field limits, URL/color/timestamp
checks, dimension coercion and total character accounting.
"""

from typing import Any, Dict, List, Optional

from .color import validate_color
from .content import sanitize_content
from .constants import (
    AUTHOR_NAME_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    FIELD_NAME_MAX_LENGTH,
    FIELD_VALUE_MAX_LENGTH,
    FOOTER_TEXT_MAX_LENGTH,
    MAX_FIELDS,
    TITLE_MAX_LENGTH,
    TOTAL_CHAR_LIMIT,
)
from .length import validate_string_length
from .models import ValidationResult
from .timestamp import validate_timestamp
from .url_validation import validate_url
from ..exceptions import (
    EmbedSanitizationError,
    EmbedValidationError,
    InvalidColorError,
    InvalidUrlError,
)


def _coerce_dimension(value: Any, field_name: str, issues: List[str]) -> Optional[int]:
    """
    Coerce a dimension (width/height) to a non-negative integer.

    Malformed values are reported as validation issues rather than raising
    an uncaught ValueError/TypeError.

    Args:
        value: Raw dimension value
        field_name: Name of field for error messages
        issues: Issue accumulator

    Returns:
        Coerced non-negative int, or None if missing/invalid
    """
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        issues.append(f"{field_name} must be an integer")
        return None

    try:
        if isinstance(value, int):
            number = value
        elif isinstance(value, float):
            if not value.is_integer():
                issues.append(f"{field_name} must be an integer")
                return None
            number = int(value)
        else:
            number = int(str(value).strip())
    except (TypeError, ValueError):
        issues.append(f"{field_name} must be an integer")
        return None

    if number < 0:
        issues.append(f"{field_name} must be a non-negative integer")
        return None

    return number


def calculate_embed_chars(embed_data: Dict[str, Any]) -> int:
    """
    Calculate total character count for an embed.

    Args:
        embed_data: Embed data dictionary

    Returns:
        Total character count
    """
    total = 0

    if embed_data.get("title"):
        total += len(str(embed_data["title"]))

    if embed_data.get("description"):
        total += len(str(embed_data["description"]))

    if embed_data.get("footer") and embed_data["footer"].get("text"):
        total += len(str(embed_data["footer"]["text"]))

    if embed_data.get("author") and embed_data["author"].get("name"):
        total += len(str(embed_data["author"]["name"]))

    fields = embed_data.get("fields", [])
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                if field.get("name"):
                    total += len(str(field["name"]))
                if field.get("value"):
                    total += len(str(field["value"]))

    return total


def validate_embed_data(embed_data: Dict[str, Any]) -> ValidationResult:
    """
    Validate complete embed data.

    Args:
        embed_data: Embed data dictionary

    Returns:
        ValidationResult with validation status and any issues
    """
    issues: List[str] = []
    sanitized: Dict[str, Any] = {}

    # Validate title
    if embed_data.get("title"):
        try:
            title = sanitize_content(str(embed_data["title"]), "title")
            title = validate_string_length(title, TITLE_MAX_LENGTH, "title")
            sanitized["title"] = title
        except (EmbedValidationError, EmbedSanitizationError) as e:
            issues.append(str(e))

    # Validate description
    if embed_data.get("description"):
        try:
            desc = sanitize_content(str(embed_data["description"]), "description")
            desc = validate_string_length(desc, DESCRIPTION_MAX_LENGTH, "description")
            sanitized["description"] = desc
        except (EmbedValidationError, EmbedSanitizationError) as e:
            issues.append(str(e))

    # Validate URL
    if embed_data.get("url"):
        try:
            sanitized["url"] = validate_url(str(embed_data["url"]), "url")
        except InvalidUrlError as e:
            issues.append(str(e))

    # Validate timestamp
    if embed_data.get("timestamp"):
        try:
            sanitized["timestamp"] = validate_timestamp(str(embed_data["timestamp"]))
        except EmbedValidationError as e:
            issues.append(str(e))

    # Validate color
    if embed_data.get("color"):
        try:
            sanitized["color"] = validate_color(str(embed_data["color"]))
        except InvalidColorError as e:
            issues.append(str(e))

    # Validate footer
    if embed_data.get("footer"):
        footer = embed_data["footer"]
        sanitized_footer = {}
        if isinstance(footer, dict):
            if footer.get("text"):
                try:
                    text = sanitize_content(str(footer["text"]), "footer.text")
                    text = validate_string_length(
                        text, FOOTER_TEXT_MAX_LENGTH, "footer.text"
                    )
                    sanitized_footer["text"] = text
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))

            if footer.get("icon_url"):
                try:
                    sanitized_footer["icon_url"] = validate_url(
                        str(footer["icon_url"]), "footer.icon_url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))
        else:
            issues.append("footer must be an object")

        if sanitized_footer:
            sanitized["footer"] = sanitized_footer

    # Validate image
    if embed_data.get("image"):
        image = embed_data["image"]
        sanitized_image = {}
        if isinstance(image, dict):
            if image.get("url"):
                try:
                    sanitized_image["url"] = validate_url(
                        str(image["url"]), "image.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))

            width = _coerce_dimension(image.get("width"), "image.width", issues)
            if width is not None:
                sanitized_image["width"] = width
            height = _coerce_dimension(image.get("height"), "image.height", issues)
            if height is not None:
                sanitized_image["height"] = height
        else:
            issues.append("image must be an object")

        if sanitized_image:
            sanitized["image"] = sanitized_image

    # Validate thumbnail
    if embed_data.get("thumbnail"):
        thumbnail = embed_data["thumbnail"]
        sanitized_thumbnail = {}
        if isinstance(thumbnail, dict):
            if thumbnail.get("url"):
                try:
                    sanitized_thumbnail["url"] = validate_url(
                        str(thumbnail["url"]), "thumbnail.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))

            width = _coerce_dimension(thumbnail.get("width"), "thumbnail.width", issues)
            if width is not None:
                sanitized_thumbnail["width"] = width
            height = _coerce_dimension(
                thumbnail.get("height"), "thumbnail.height", issues
            )
            if height is not None:
                sanitized_thumbnail["height"] = height
        else:
            issues.append("thumbnail must be an object")

        if sanitized_thumbnail:
            sanitized["thumbnail"] = sanitized_thumbnail

    # Validate author
    if embed_data.get("author"):
        author = embed_data["author"]
        sanitized_author = {}
        if isinstance(author, dict):
            if author.get("name"):
                try:
                    name = sanitize_content(str(author["name"]), "author.name")
                    name = validate_string_length(
                        name, AUTHOR_NAME_MAX_LENGTH, "author.name"
                    )
                    sanitized_author["name"] = name
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))

            if author.get("url"):
                try:
                    sanitized_author["url"] = validate_url(
                        str(author["url"]), "author.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))

            if author.get("icon_url"):
                try:
                    sanitized_author["icon_url"] = validate_url(
                        str(author["icon_url"]), "author.icon_url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))
        else:
            issues.append("author must be an object")

        if sanitized_author:
            sanitized["author"] = sanitized_author

    # Validate provider
    if embed_data.get("provider"):
        provider = embed_data["provider"]
        sanitized_provider = {}
        if isinstance(provider, dict):
            if provider.get("name"):
                try:
                    sanitized_provider["name"] = sanitize_content(
                        str(provider["name"]), "provider.name"
                    )
                except EmbedSanitizationError as e:
                    issues.append(str(e))

            if provider.get("url"):
                try:
                    sanitized_provider["url"] = validate_url(
                        str(provider["url"]), "provider.url"
                    )
                except InvalidUrlError as e:
                    issues.append(str(e))
        else:
            issues.append("provider must be an object")

        if sanitized_provider:
            sanitized["provider"] = sanitized_provider

    # Validate fields
    fields = embed_data.get("fields", [])
    if not isinstance(fields, list):
        issues.append("fields must be a list")
    elif len(fields) > MAX_FIELDS:
        issues.append(f"Embed cannot have more than {MAX_FIELDS} fields")
    else:
        sanitized_fields = []
        for i, field in enumerate(fields):
            sanitized_field = {}

            if not isinstance(field, dict):
                issues.append(f"fields[{i}] must be an object")
                continue

            if field.get("name") is not None:
                try:
                    name = sanitize_content(str(field["name"]), f"fields[{i}].name")
                    name = validate_string_length(
                        name, FIELD_NAME_MAX_LENGTH, f"fields[{i}].name"
                    )
                    sanitized_field["name"] = name
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))
            else:
                issues.append(f"fields[{i}].name is required")

            if field.get("value") is not None:
                try:
                    value = sanitize_content(str(field["value"]), f"fields[{i}].value")
                    value = validate_string_length(
                        value, FIELD_VALUE_MAX_LENGTH, f"fields[{i}].value"
                    )
                    sanitized_field["value"] = value
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))
            else:
                issues.append(f"fields[{i}].value is required")

            sanitized_field["inline"] = bool(field.get("inline", False))
            sanitized_fields.append(sanitized_field)

        if sanitized_fields:
            sanitized["fields"] = sanitized_fields

    # Calculate total characters
    total_chars = calculate_embed_chars(sanitized if sanitized else embed_data)

    if total_chars > TOTAL_CHAR_LIMIT:
        issues.append(
            f"Total embed characters ({total_chars}) exceeds limit of "
            f"{TOTAL_CHAR_LIMIT}"
        )

    return ValidationResult(
        valid=len(issues) == 0,
        issues=issues,
        total_chars=total_chars,
        sanitized_data=sanitized if len(issues) == 0 else None,
    )
