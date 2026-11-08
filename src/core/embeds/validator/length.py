"""
String length validation.

Length checks for embed fields.
"""

from ..exceptions import EmbedValidationError


def validate_string_length(value: str, max_length: int, field_name: str) -> str:
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
            [f"{field_name}: {len(value)}/{max_length}"],
        )

    return value
