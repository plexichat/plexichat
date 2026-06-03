"""
Protocol mixin for the MessageService sub-package.

Defines the protocol interface that implementing classes must satisfy
to be used as part of the MessageService composed via MRO.
"""

from typing import Any, Dict, List, Optional, Protocol

from src.core.base import SnowflakeID

from ...models import Message, MessageType


class MessageServiceProtocol(Protocol):
    """Protocol defining methods used across MessageService mixins."""

    # Dependencies
    _db: Any
    _repo: Any
    _attachment_repo: Any
    _pin_repo: Any
    _conversation_repo: Any
    _status_repo: Any
    _participant_svc: Any
    _user_settings_svc: Any
    _content_filter_svc: Any
    _ratchet_manager: Any

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Forward init through MRO chain.
        Protocol metaclass generates a synthetic __init__ that doesn't
        call super().__init__(), which would break the MRO chain when
        this Protocol is used as a mixin base class.
        """
        super().__init__(*args, **kwargs)

    # === Sending ===

    def send_message(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        content: str,
        message_type: MessageType = MessageType.TEXT,
        reply_to_id: Optional[SnowflakeID] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        webhook_id: Optional[SnowflakeID] = None,
    ) -> Message: ...

    def send_system_message(
        self,
        conversation_id: SnowflakeID,
        content: str,
        event_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message: ...

    def _normalize_url(self, url: str) -> str: ...

    # === Editing & Deleting ===

    def edit_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, content: str
    ) -> Message: ...

    def update_message_metadata(
        self,
        message_id: SnowflakeID,
        metadata: Optional[Dict[str, Any]],
        merge: bool = True,
    ) -> Message: ...

    def delete_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, hard_delete: bool = False
    ) -> bool: ...

    def delete_messages_bulk(
        self,
        user_id: SnowflakeID,
        message_ids: List[SnowflakeID],
        hard_delete: bool = False,
    ) -> Dict[str, Any]: ...

    def archive_messages_bulk(
        self,
        user_id: SnowflakeID,
        message_ids: List[SnowflakeID],
    ) -> Dict[str, Any]: ...

    # === Retrieval ===

    def get_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> Optional[Message]: ...

    def get_messages(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Message]: ...

    def search_messages(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        query: str,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Message]: ...

    def get_message_raw(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]: ...

    # === BaseService helpers ===

    def _get_timestamp(self) -> int: ...

    def _generate_id(self) -> SnowflakeID: ...

    def _get_config(self, key: str, default: Any = None) -> Any: ...
