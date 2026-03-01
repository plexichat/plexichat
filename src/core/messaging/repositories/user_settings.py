"""
User settings repository - Data access for user message and filter settings.
"""

from typing import Any, Dict, List, Optional

from ..models import ContentFilter, UserMessageSettings, FilterAction
from .base import BaseRepository
from src.core.base import SnowflakeID


class UserSettingsRepository(BaseRepository[UserMessageSettings]):
    """Repository for user settings data access."""

    # === Message Settings ===

    def get_message_settings(self, user_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get user's message settings."""
        return self._fetch_one(
            "SELECT * FROM msg_user_settings WHERE user_id = ?",
            (user_id,),
        )

    def create_message_settings(
        self,
        user_id: SnowflakeID,
        allow_dms_from: str = "everyone",
        auto_create_dms: bool = True,
        max_message_length: Optional[int] = None,
        max_attachment_size: Optional[int] = None,
        max_attachments_per_message: Optional[int] = None,
        read_receipts_enabled: bool = True,
        typing_indicators_enabled: bool = True,
        compact_messages_enabled: bool = True,
        auto_commit: bool = True,
    ) -> None:
        """Create user message settings."""
        self._execute(
            """INSERT INTO msg_user_settings 
               (user_id, allow_dms_from, auto_create_dms, max_message_length, 
                max_attachment_size, max_attachments_per_message, 
                read_receipts_enabled, typing_indicators_enabled, compact_messages_enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                allow_dms_from,
                1 if auto_create_dms else 0,
                max_message_length,
                max_attachment_size,
                max_attachments_per_message,
                1 if read_receipts_enabled else 0,
                1 if typing_indicators_enabled else 0,
                1 if compact_messages_enabled else 0,
            ),
            auto_commit=auto_commit,
        )

    def update_message_settings(
        self,
        user_id: SnowflakeID,
        allow_dms_from: str,
        auto_create_dms: bool,
        max_message_length: Optional[int],
        max_attachment_size: Optional[int],
        max_attachments_per_message: Optional[int],
        read_receipts_enabled: bool,
        typing_indicators_enabled: bool,
        compact_messages_enabled: bool,
        auto_commit: bool = True,
    ) -> None:
        """Update user message settings."""
        self._execute(
            """UPDATE msg_user_settings 
               SET allow_dms_from = ?, auto_create_dms = ?, max_message_length = ?,
                   max_attachment_size = ?, max_attachments_per_message = ?,
                   read_receipts_enabled = ?, typing_indicators_enabled = ?,
                   compact_messages_enabled = ?
               WHERE user_id = ?""",
            (
                allow_dms_from,
                1 if auto_create_dms else 0,
                max_message_length,
                max_attachment_size,
                max_attachments_per_message,
                1 if read_receipts_enabled else 0,
                1 if typing_indicators_enabled else 0,
                1 if compact_messages_enabled else 0,
                user_id,
            ),
            auto_commit=auto_commit,
        )

    def message_settings_exists(self, user_id: SnowflakeID) -> bool:
        """Check if user has message settings."""
        row = self._fetch_one(
            "SELECT 1 FROM msg_user_settings WHERE user_id = ?",
            (user_id,),
        )
        return row is not None

    def row_to_message_settings(self, row: Dict[str, Any]) -> UserMessageSettings:
        """Convert database row to UserMessageSettings model."""
        return UserMessageSettings(
            user_id=row["user_id"],
            allow_dms_from=row["allow_dms_from"] or "everyone",
            auto_create_dms=bool(row["auto_create_dms"]),
            max_message_length=row["max_message_length"],
            max_attachment_size=row["max_attachment_size"],
            max_attachments_per_message=row["max_attachments_per_message"],
            read_receipts_enabled=bool(row["read_receipts_enabled"]),
            typing_indicators_enabled=bool(row["typing_indicators_enabled"]),
            compact_messages_enabled=bool(row.get("compact_messages_enabled", True)),
        )

    # === Content Filter Settings ===

    def get_filter_settings(self, user_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get user's content filter settings."""
        return self._fetch_one(
            "SELECT * FROM msg_content_filters WHERE user_id = ?",
            (user_id,),
        )

    def create_filter_settings(
        self,
        user_id: SnowflakeID,
        profanity_filter: bool = False,
        nsfw_filter: bool = False,
        spoiler_click_to_reveal: bool = True,
        custom_blocked_words: Optional[List[str]] = None,
        filter_action: FilterAction = FilterAction.CENSOR,
        auto_commit: bool = True,
    ) -> None:
        """Create user content filter settings."""
        import json

        self._execute(
            """INSERT INTO msg_content_filters 
               (user_id, profanity_filter, nsfw_filter, spoiler_click_to_reveal, 
                custom_blocked_words, filter_action)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                1 if profanity_filter else 0,
                1 if nsfw_filter else 0,
                1 if spoiler_click_to_reveal else 0,
                json.dumps(custom_blocked_words or []),
                filter_action.value,
            ),
            auto_commit=auto_commit,
        )

    def update_filter_settings(
        self,
        user_id: SnowflakeID,
        profanity_filter: bool,
        nsfw_filter: bool,
        spoiler_click_to_reveal: bool,
        custom_blocked_words: List[str],
        filter_action: FilterAction,
        auto_commit: bool = True,
    ) -> None:
        """Update user content filter settings."""
        import json

        self._execute(
            """UPDATE msg_content_filters 
               SET profanity_filter = ?, nsfw_filter = ?, spoiler_click_to_reveal = ?, 
                   custom_blocked_words = ?, filter_action = ?
               WHERE user_id = ?""",
            (
                1 if profanity_filter else 0,
                1 if nsfw_filter else 0,
                1 if spoiler_click_to_reveal else 0,
                json.dumps(custom_blocked_words),
                filter_action.value,
                user_id,
            ),
            auto_commit=auto_commit,
        )

    def filter_settings_exists(self, user_id: SnowflakeID) -> bool:
        """Check if user has filter settings."""
        row = self._fetch_one(
            "SELECT 1 FROM msg_content_filters WHERE user_id = ?",
            (user_id,),
        )
        return row is not None

    def row_to_filter_settings(self, row: Dict[str, Any]) -> ContentFilter:
        """Convert database row to ContentFilter model."""
        import json

        return ContentFilter(
            user_id=row["user_id"],
            profanity_filter=bool(row["profanity_filter"]),
            nsfw_filter=bool(row["nsfw_filter"]),
            spoiler_click_to_reveal=bool(row["spoiler_click_to_reveal"]),
            custom_blocked_words=json.loads(row["custom_blocked_words"])
            if row["custom_blocked_words"]
            else [],
            filter_action=FilterAction(row["filter_action"])
            if row["filter_action"]
            else FilterAction.CENSOR,
        )

    # === User Existence Check ===

    def user_exists(self, user_id: SnowflakeID) -> bool:
        """Check if a user exists in the auth system."""
        row = self._fetch_one(
            "SELECT 1 FROM auth_users WHERE id = ?",
            (user_id,),
        )
        return row is not None

    def users_exist_batch(self, user_ids: List[SnowflakeID]) -> Dict[SnowflakeID, bool]:
        """Check if multiple users exist (batch operation)."""
        if not user_ids:
            return {}

        in_clause, params = self._build_in_clause(user_ids)
        rows = self._fetch_all(
            f"SELECT id FROM auth_users WHERE id IN {in_clause}",  # nosec B608
            params,
        )

        existing = {row["id"] for row in rows}
        return {uid: uid in existing for uid in user_ids}

