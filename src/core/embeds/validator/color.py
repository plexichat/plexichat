"""
Color validation.

Hex color normalization for embeds.
"""

from .constants import COLOR_PATTERN
from ..exceptions import InvalidColorError


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
        raise InvalidColorError(
            "Color must be a valid hex color (e.g., #FF0000)", color
        )

    # Normalize to include #
    if not color.startswith("#"):
        color = "#" + color

    # Expand 3-char to 6-char
    if len(color) == 4:
        color = "#" + color[1] * 2 + color[2] * 2 + color[3] * 2

    return color.upper()
