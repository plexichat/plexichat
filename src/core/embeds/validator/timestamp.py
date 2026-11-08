"""
Timestamp validation.

ISO8601 timestamps for embeds.
"""

from .constants import TIMESTAMP_PATTERN
from ..exceptions import EmbedValidationError


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
