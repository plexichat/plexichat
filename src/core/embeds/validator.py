"""
Embed validator - Validation and sanitization for embed content.

Handles field limits, character limits, URL validation, and XSS prevention.
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from .exceptions import (
    EmbedValidationError,
    EmbedFieldLimitError,
    EmbedCharacterLimitError,
    InvalidUrlError,
    InvalidColorError,
    EmbedSanitizationError,
)


# Character limits for embeds
TITLE_MAX_LENGTH = 256
DESCRIPTION_MAX_LENGTH = 4096
FIELD_NAME_MAX_LENGTH = 256
FIELD_VALUE_MAX_LENGTH = 1024
FOOTER_TEXT_MAX_LENGTH = 2048
AUTHOR_NAME_MAX_LENGTH = 256
TOTAL_CHAR_LIMIT = 6000
MAX_FIELDS = 25
MAX_EMBEDS_PER_MESSAGE = 10

# URL validation pattern (http/https only)
URL_PATTERN = re.compile(
    r'^https?://'
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
    r'localhost|'
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$',
    re.IGNORECASE
)

# Color validation pattern (hex color)
COLOR_PATTERN = re.compile(r'^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$')

# ISO8601 timestamp pattern
TIMESTAMP_PATTERN = re.compile(
    r'^\d{4}-\d{2}-\d{2}'
    r'(?:T\d{2}:\d{2}:\d{2}'
    r'(?:\.\d+)?'
    r'(?:Z|[+-]\d{2}:?\d{2})?)?$'
)

# Dangerous patterns for XSS prevention
DANGEROUS_PATTERNS = [
    re.compile(r'<script', re.IGNORECASE),
    re.compile(r'javascript:', re.IGNORECASE),
    re.compile(r'data:', re.IGNORECASE),
    re.compile(r'vbscript:', re.IGNORECASE),
    re.compile(r'on\w+\s*=', re.IGNORECASE),
    re.compile(r'<iframe', re.IGNORECASE),
    re.compile(r'<object', re.IGNORECASE),
    re.compile(r'<embed', re.IGNORECASE),
]


@dataclass
class ValidationResult:
    """Result of embed validation."""
    valid: bool
    issues: List[str]
    total_chars: int
    sanitized_data: Optional[Dict[str, Any]] = None


def validate_url(url: str, field_name: str = "url") -> str:
    """
    Validate and sanitize a URL.
    
    Args:
        url: URL to validate
        field_name: Name of field for error messages
        
    Returns:
        Sanitized URL
        
    Raises:
        InvalidUrlError: If URL is invalid or unsafe
    """
    if not url:
        return url
    
    url = url.strip()
    
    # Check for dangerous protocols
    lower_url = url.lower()
    if lower_url.startswith('javascript:'):
        raise InvalidUrlError(f"JavaScript URLs not allowed in {field_name}", url)
    if lower_url.startswith('data:'):
        raise InvalidUrlError(f"Data URLs not allowed in {field_name}", url)
    if lower_url.startswith('vbscript:'):
        raise InvalidUrlError(f"VBScript URLs not allowed in {field_name}", url)
    
    # Must be http or https
    if not (lower_url.startswith('http://') or lower_url.startswith('https://')):
        raise InvalidUrlError(f"URL must use http or https protocol in {field_name}", url)
    
    # Validate URL format
    if not URL_PATTERN.match(url):
        raise InvalidUrlError(f"Invalid URL format in {field_name}", url)
    
    return url


def validate_color(color: str) -> str:
    """
    Validate and normalize a hex color.
    
    Args:
        color: Color to validate (with or without #)
        
    Returns:
        Normalized color with # prefix
        
    Raises:
        InvalidColorError: If color format is invalid
    """
    if not color:
        return color
    
    color = color.strip()
    
    if not COLOR_PATTERN.match(color):
        raise InvalidColorError("Color must be a valid hex color (e.g., #FF0000)", color)
    
    # Normalize to include #
    if not color.startswith('#'):
        color = '#' + color
    
    # Expand 3-char to 6-char
    if len(color) == 4:
        color = '#' + color[1] * 2 + color[2] * 2 + color[3] * 2
    
    return color.upper()


def validate_timestamp(timestamp: str) -> str:
    """
    Validate an ISO8601 timestamp.
    
    Args:
        timestamp: Timestamp to validate
        
    Returns:
        Validated timestamp
        
    Raises:
        EmbedValidationError: If timestamp format is invalid
    """
    if not timestamp:
        return timestamp
    
    timestamp = timestamp.strip()
    
    if not TIMESTAMP_PATTERN.match(timestamp):
        raise EmbedValidationError("Timestamp must be in ISO8601 format")
    
    return timestamp


def sanitize_content(content: str, field_name: str = "content") -> str:
    """
    Sanitize text content to prevent XSS.
    
    Args:
        content: Content to sanitize
        field_name: Name of field for error messages
        
    Returns:
        Sanitized content
        
    Raises:
        EmbedSanitizationError: If content contains dangerous patterns
    """
    if not content:
        return content
    
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(content):
            raise EmbedSanitizationError(
                f"Potentially unsafe content detected in {field_name}",
                field_name
            )
    
    return content


def validate_string_length(
    value: str,
    max_length: int,
    field_name: str
) -> str:
    """
    Validate string length.
    
    Args:
        value: String to validate
        max_length: Maximum allowed length
        field_name: Name of field for error messages
        
    Returns:
        Validated string
        
    Raises:
        EmbedValidationError: If string exceeds max length
    """
    if not value:
        return value
    
    if len(value) > max_length:
        raise EmbedValidationError(
            f"{field_name} exceeds maximum length of {max_length} characters",
            [f"{field_name}: {len(value)}/{max_length}"]
        )
    
    return value


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
        total += len(embed_data["title"])
    
    if embed_data.get("description"):
        total += len(embed_data["description"])
    
    if embed_data.get("footer") and embed_data["footer"].get("text"):
        total += len(embed_data["footer"]["text"])
    
    if embed_data.get("author") and embed_data["author"].get("name"):
        total += len(embed_data["author"]["name"])
    
    for field in embed_data.get("fields", []):
        if field.get("name"):
            total += len(field["name"])
        if field.get("value"):
            total += len(field["value"])
    
    return total


def validate_embed_data(embed_data: Dict[str, Any]) -> ValidationResult:
    """
    Validate complete embed data.
    
    Args:
        embed_data: Embed data dictionary
        
    Returns:
        ValidationResult with validation status and any issues
    """
    issues = []
    sanitized = {}
    
    # Validate title
    if embed_data.get("title"):
        try:
            title = sanitize_content(embed_data["title"], "title")
            title = validate_string_length(title, TITLE_MAX_LENGTH, "title")
            sanitized["title"] = title
        except (EmbedValidationError, EmbedSanitizationError) as e:
            issues.append(str(e))
    
    # Validate description
    if embed_data.get("description"):
        try:
            desc = sanitize_content(embed_data["description"], "description")
            desc = validate_string_length(desc, DESCRIPTION_MAX_LENGTH, "description")
            sanitized["description"] = desc
        except (EmbedValidationError, EmbedSanitizationError) as e:
            issues.append(str(e))
    
    # Validate URL
    if embed_data.get("url"):
        try:
            sanitized["url"] = validate_url(embed_data["url"], "url")
        except InvalidUrlError as e:
            issues.append(str(e))
    
    # Validate timestamp
    if embed_data.get("timestamp"):
        try:
            sanitized["timestamp"] = validate_timestamp(embed_data["timestamp"])
        except EmbedValidationError as e:
            issues.append(str(e))
    
    # Validate color
    if embed_data.get("color"):
        try:
            sanitized["color"] = validate_color(embed_data["color"])
        except InvalidColorError as e:
            issues.append(str(e))
    
    # Validate footer
    if embed_data.get("footer"):
        footer = embed_data["footer"]
        sanitized_footer = {}
        
        if footer.get("text"):
            try:
                text = sanitize_content(footer["text"], "footer.text")
                text = validate_string_length(text, FOOTER_TEXT_MAX_LENGTH, "footer.text")
                sanitized_footer["text"] = text
            except (EmbedValidationError, EmbedSanitizationError) as e:
                issues.append(str(e))
        
        if footer.get("icon_url"):
            try:
                sanitized_footer["icon_url"] = validate_url(footer["icon_url"], "footer.icon_url")
            except InvalidUrlError as e:
                issues.append(str(e))
        
        if sanitized_footer:
            sanitized["footer"] = sanitized_footer
    
    # Validate image
    if embed_data.get("image"):
        image = embed_data["image"]
        sanitized_image = {}
        
        if image.get("url"):
            try:
                sanitized_image["url"] = validate_url(image["url"], "image.url")
            except InvalidUrlError as e:
                issues.append(str(e))
        
        if image.get("width"):
            sanitized_image["width"] = int(image["width"])
        if image.get("height"):
            sanitized_image["height"] = int(image["height"])
        
        if sanitized_image:
            sanitized["image"] = sanitized_image
    
    # Validate thumbnail
    if embed_data.get("thumbnail"):
        thumbnail = embed_data["thumbnail"]
        sanitized_thumbnail = {}
        
        if thumbnail.get("url"):
            try:
                sanitized_thumbnail["url"] = validate_url(thumbnail["url"], "thumbnail.url")
            except InvalidUrlError as e:
                issues.append(str(e))
        
        if thumbnail.get("width"):
            sanitized_thumbnail["width"] = int(thumbnail["width"])
        if thumbnail.get("height"):
            sanitized_thumbnail["height"] = int(thumbnail["height"])
        
        if sanitized_thumbnail:
            sanitized["thumbnail"] = sanitized_thumbnail
    
    # Validate author
    if embed_data.get("author"):
        author = embed_data["author"]
        sanitized_author = {}
        
        if author.get("name"):
            try:
                name = sanitize_content(author["name"], "author.name")
                name = validate_string_length(name, AUTHOR_NAME_MAX_LENGTH, "author.name")
                sanitized_author["name"] = name
            except (EmbedValidationError, EmbedSanitizationError) as e:
                issues.append(str(e))
        
        if author.get("url"):
            try:
                sanitized_author["url"] = validate_url(author["url"], "author.url")
            except InvalidUrlError as e:
                issues.append(str(e))
        
        if author.get("icon_url"):
            try:
                sanitized_author["icon_url"] = validate_url(author["icon_url"], "author.icon_url")
            except InvalidUrlError as e:
                issues.append(str(e))
        
        if sanitized_author:
            sanitized["author"] = sanitized_author
    
    # Validate provider
    if embed_data.get("provider"):
        provider = embed_data["provider"]
        sanitized_provider = {}
        
        if provider.get("name"):
            try:
                sanitized_provider["name"] = sanitize_content(provider["name"], "provider.name")
            except EmbedSanitizationError as e:
                issues.append(str(e))
        
        if provider.get("url"):
            try:
                sanitized_provider["url"] = validate_url(provider["url"], "provider.url")
            except InvalidUrlError as e:
                issues.append(str(e))
        
        if sanitized_provider:
            sanitized["provider"] = sanitized_provider
    
    # Validate fields
    fields = embed_data.get("fields", [])
    if len(fields) > MAX_FIELDS:
        issues.append(f"Embed cannot have more than {MAX_FIELDS} fields")
    else:
        sanitized_fields = []
        for i, field in enumerate(fields):
            sanitized_field = {}
            
            if field.get("name"):
                try:
                    name = sanitize_content(field["name"], f"fields[{i}].name")
                    name = validate_string_length(name, FIELD_NAME_MAX_LENGTH, f"fields[{i}].name")
                    sanitized_field["name"] = name
                except (EmbedValidationError, EmbedSanitizationError) as e:
                    issues.append(str(e))
            else:
                issues.append(f"fields[{i}].name is required")
            
            if field.get("value"):
                try:
                    value = sanitize_content(field["value"], f"fields[{i}].value")
                    value = validate_string_length(value, FIELD_VALUE_MAX_LENGTH, f"fields[{i}].value")
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
        issues.append(f"Total embed characters ({total_chars}) exceeds limit of {TOTAL_CHAR_LIMIT}")
    
    return ValidationResult(
        valid=len(issues) == 0,
        issues=issues,
        total_chars=total_chars,
        sanitized_data=sanitized if len(issues) == 0 else None
    )
