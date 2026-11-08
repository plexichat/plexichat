"""
Embed validator - Validation and sanitization for embed content.

Handles field limits, character limits, URL validation, and XSS prevention.

This package is the split successor of the legacy ``src.core.embeds.validator``
module. It is a compatibility shim: every name that was importable from the
old module remains importable from this package, including the private
helpers tests may monkeypatch. Logic lives in single-responsibility
submodules (``constants``, ``models``, ``url_validation``, ``content``,
``color``, ``timestamp``, ``length``, ``schema``, ``message``).
"""

from __future__ import annotations

from .color import validate_color
from .constants import (
    AUTHOR_NAME_MAX_LENGTH,
    COLOR_PATTERN,
    DESCRIPTION_MAX_LENGTH,
    DANGEROUS_PATTERNS,
    FIELD_NAME_MAX_LENGTH,
    FIELD_VALUE_MAX_LENGTH,
    FOOTER_TEXT_MAX_LENGTH,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_FIELDS,
    MAX_URL_LENGTH,
    TIMESTAMP_PATTERN,
    TITLE_MAX_LENGTH,
    TOTAL_CHAR_LIMIT,
    URL_ATTRIBUTE_BREAKOUT_PATTERN,
    URL_PATTERN,
    _RELATIVE_MEDIA_PATTERN,
    _URL_CONTROL_CHAR_PATTERN,
)
from .content import sanitize_content
from .length import validate_string_length
from .message import sanitize_message_embeds
from .models import ValidationResult
from .schema import _coerce_dimension, calculate_embed_chars, validate_embed_data
from .timestamp import validate_timestamp
from .url_validation import (
    _public_media_origin,
    _strip_url_dangerous_unicode,
    validate_url,
)

from ..exceptions import (
    EmbedSanitizationError,
    EmbedValidationError,
    InvalidColorError,
    InvalidUrlError,
)

__all__ = [
    # Models
    "ValidationResult",
    # Validation functions
    "validate_url",
    "validate_color",
    "validate_timestamp",
    "sanitize_content",
    "validate_string_length",
    "sanitize_message_embeds",
    "validate_embed_data",
    "calculate_embed_chars",
    # Exceptions (re-exported to preserve the legacy module namespace)
    "EmbedValidationError",
    "InvalidUrlError",
    "InvalidColorError",
    "EmbedSanitizationError",
    # Limits
    "TITLE_MAX_LENGTH",
    "DESCRIPTION_MAX_LENGTH",
    "FIELD_NAME_MAX_LENGTH",
    "FIELD_VALUE_MAX_LENGTH",
    "FOOTER_TEXT_MAX_LENGTH",
    "AUTHOR_NAME_MAX_LENGTH",
    "TOTAL_CHAR_LIMIT",
    "MAX_FIELDS",
    "MAX_EMBEDS_PER_MESSAGE",
    "MAX_URL_LENGTH",
    # Patterns
    "URL_PATTERN",
    "URL_ATTRIBUTE_BREAKOUT_PATTERN",
    "_URL_CONTROL_CHAR_PATTERN",
    "_RELATIVE_MEDIA_PATTERN",
    "COLOR_PATTERN",
    "TIMESTAMP_PATTERN",
    "DANGEROUS_PATTERNS",
    # Private helpers kept for monkeypatch/test compatibility
    "_strip_url_dangerous_unicode",
    "_public_media_origin",
    "_coerce_dimension",
]
