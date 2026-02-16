"""
User settings service - Business logic for user message settings.
"""

from typing import Any, Dict, List, Optional

from ..models import UserMessageSettings
from ..repositories.user_settings import UserSettingsRepository
from .base import BaseService
from src.core.base import SnowflakeID
from src.core.database import cached


class UserSettingsService(BaseService):
    """Service for user message settings operations."""

    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self._repo = UserSettingsRepository(db)

    def get_message_settings(self, user_id: SnowflakeID) -> UserMessageSettings:
        """Get user's message settings (cached)."""
        cache_key = ("msg_settings", user_id)

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        row = self._repo.get_message_settings(user_id)

        if row:
            result = self._repo.row_to_message_settings(row)
        else:
            result = UserMessageSettings(user_id=user_id)

        self._cache_set(cache_key, result)
        return result

    def update_message_settings(
        self,
        user_id: SnowflakeID,
        allow_dms_from: Optional[str] = None,
        auto_create_dms: Optional[bool] = None,
        max_message_length: Optional[int] = None,
        max_attachment_size: Optional[int] = None,
        max_attachments_per_message: Optional[int] = None,
        read_receipts_enabled: Optional[bool] = None,
        typing_indicators_enabled: Optional[bool] = None,
        compact_messages_enabled: Optional[bool] = None,
    ) -> UserMessageSettings:
        """Update user's message settings."""
        current = self.get_message_settings(user_id)

        new_dms = allow_dms_from if allow_dms_from is not None else current.allow_dms_from
        new_auto = auto_create_dms if auto_create_dms is not None else current.auto_create_dms
        new_length = max_message_length if max_message_length is not None else current.max_message_length
        new_att_size = max_attachment_size if max_attachment_size is not None else current.max_attachment_size
        new_att_count = max_attachments_per_message if max_attachments_per_message is not None else current.max_attachments_per_message
        new_read_receipts = read_receipts_enabled if read_receipts_enabled is not None else current.read_receipts_enabled
        new_typing = typing_indicators_enabled if typing_indicators_enabled is not None else current.typing_indicators_enabled
        new_compact = compact_messages_enabled if compact_messages_enabled is not None else current.compact_messages_enabled

        if self._repo.message_settings_exists(user_id):
            self._repo.update_message_settings(
                user_id,
                new_dms,
                new_auto,
                new_length,
                new_att_size,
                new_att_count,
                new_read_receipts,
                new_typing,
                new_compact,
            )
        else:
            self._repo.create_message_settings(
                user_id,
                new_dms,
                new_auto,
                new_length,
                new_att_size,
                new_att_count,
                new_read_receipts,
                new_typing,
                new_compact,
            )

        # Invalidate cache
        self._cache_invalidate(("msg_settings", user_id))

        return self.get_message_settings(user_id)

    @cached(ttl=300, prefix="user_exists")
    def user_exists(self, user_id: SnowflakeID) -> bool:
        """Check if a user exists."""
        return self._repo.user_exists(user_id)

    @cached(ttl=300, prefix="users_exist_batch")
    def users_exist_batch(self, user_ids: List[SnowflakeID]) -> Dict[SnowflakeID, bool]:
        """Check if multiple users exist (batch operation)."""
        return self._repo.users_exist_batch(user_ids)
