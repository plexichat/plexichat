"""
Ratchet interval value object.

A ``RatchetInterval`` represents a single ratchet key range for a
channel. The range is half-open in the message-id space: a message
belongs to the interval when ``start_message_id <= msg_id`` and
either ``end_message_id`` is ``None`` (open interval) or
``msg_id < end_message_id``.

The snowflake id type is imported under ``TYPE_CHECKING`` only to
avoid a circular import via ``src.core.base`` -> ``src.utils.encryption``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.core.base import SnowflakeID


@dataclass
class RatchetInterval:
    """A single ratchet key range for one channel."""

    interval_id: SnowflakeID
    conversation_id: SnowflakeID
    start_message_id: SnowflakeID
    end_message_id: Optional[SnowflakeID]
    start_key: bytes
    created_at: int
    last_message_at: int

    def contains(self, message_id: SnowflakeID) -> bool:
        """Return True if ``message_id`` falls within this interval."""
        if message_id < self.start_message_id:
            return False
        if self.end_message_id is None:
            return True
        return message_id < self.end_message_id

    def to_dict(self) -> dict:
        """Convert to a JSON-safe dict for API responses and logs.

        The ``start_key`` material is intentionally NOT included. The
        raw key is only ever held in memory; clients receive a derived
        CryptoKey via the channel ratchet API, never the start key
        itself.
        """
        return {
            "interval_id": int(self.interval_id),
            "conversation_id": int(self.conversation_id),
            "start_message_id": int(self.start_message_id),
            "end_message_id": (
                int(self.end_message_id) if self.end_message_id is not None else None
            ),
            "created_at": int(self.created_at),
            "last_message_at": int(self.last_message_at),
        }
