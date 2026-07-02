"""Protocol for the channel ratchet manager.

Declares the attributes and methods that are shared across the
``ChannelRatchetStore`` and ``ChannelRatchetManager`` boundary.
This mirrors the pattern used elsewhere in the encryption utility
(see ``kek_migration/protocol.py``).

The Protocol here is a *structural* type for pyright; it is not
intended to be instantiated on its own. The actual implementation
lives in :class:`ChannelRatchetManager`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol

if TYPE_CHECKING:
    from src.core.base import SnowflakeID


class ChannelRatchetProtocol(Protocol):
    """Structural interface for the channel ratchet manager."""

    _db: Any
    _keyring: Any
    _config: Dict[str, Any]

    def get_active_interval(self, conversation_id: SnowflakeID) -> Optional[Any]: ...

    def open_interval(
        self,
        conversation_id: SnowflakeID,
        start_message_id: SnowflakeID,
    ) -> Any: ...

    def rotate_if_due(
        self,
        conversation_id: SnowflakeID,
        last_message_id: SnowflakeID,
        now: int,
    ) -> Any: ...

    def split_on_delete(
        self,
        conversation_id: SnowflakeID,
        deleted_message_id: SnowflakeID,
        now: int,
    ) -> Optional[Any]: ...
