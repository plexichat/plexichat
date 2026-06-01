"""
Edit and delete message operations mixin for the MessageService.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_set, cache_delete
from src.utils.encryption import (
    encrypt_message,
    blind_index,
)

from ...models import Message, ParticipantRole
from ...exceptions import (
    MessageNotFoundError,
    MessageAccessDeniedError,
    InvalidContentError,
)
from ..base import BaseService
from .protocol import MessageServiceProtocol
from .search_helpers import search_index_message, search_remove_from_index


class EditDeleteMixin(BaseService, MessageServiceProtocol):
    """Mixin providing edit and delete operations."""

    def edit_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, content: str
    ) -> Message:
        """Edit a message (own messages only)."""
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row or msg_row["deleted"]:
            raise MessageNotFoundError("Message not found")

        if not self._participant_svc.is_participant(
            msg_row["conversation_id"], user_id
        ):
            raise MessageNotFoundError("Message not found")

        if msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only edit own messages")

        user_settings = self._user_settings_svc.get_message_settings(user_id)
        max_length = user_settings.max_message_length or self._get_config(
            "max_message_length", 4000
        )
        user_filter = self._content_filter_svc.get_filter_settings(user_id)

        content_result = self._content_filter_svc.validate_content(
            content, user_filter, max_length
        )
        if not content_result.valid:
            raise InvalidContentError("Invalid message content", content_result.issues)

        now = self._get_timestamp()
        final_content = content_result.sanitized_content
        content_idx = blind_index(final_content, "message_content")

        if self._get_config("encrypt_messages", True):
            final_content = encrypt_message(final_content, message_id)

        self._repo.update_content(
            message_id, final_content, now, content_index=content_idx
        )

        try:
            search_index_message(
                message_id,
                content_result.sanitized_content,
                {
                    "author_id": user_id,
                    "conversation_id": msg_row["conversation_id"],
                    "created_at": msg_row["created_at"],
                    "source_updated_at": now,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to update search index for message {message_id}: {e}")

        cache_delete(f"msg:obj:{message_id}")

        msg = self.get_message(user_id, message_id)
        if msg is None:
            raise MessageNotFoundError("Failed to retrieve updated message")

        cache_set(f"msg:obj:{message_id}", msg, ttl=3600)
        return msg

    def update_message_metadata(
        self,
        message_id: SnowflakeID,
        metadata: Optional[Dict[str, Any]],
        merge: bool = True,
    ) -> Message:
        """Update message metadata."""
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row or msg_row.get("deleted"):
            raise MessageNotFoundError("Message not found")

        existing_metadata = (
            self._repo._json_loads(msg_row["metadata"])
            if msg_row.get("metadata")
            else None
        )
        if merge and existing_metadata and metadata:
            merged = {**existing_metadata, **metadata}
        elif merge and existing_metadata and metadata is None:
            merged = existing_metadata
        else:
            merged = metadata

        now = self._get_timestamp()
        self._repo.update_metadata(message_id, merged, now)

        cache_delete(f"msg:obj:{message_id}")

        msg = self.get_message(msg_row["author_id"], message_id)
        if msg is None:
            raise MessageNotFoundError("Failed to retrieve updated message")
        cache_set(f"msg:obj:{message_id}", msg, ttl=3600)
        return msg

    def delete_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, hard_delete: bool = False
    ) -> bool:
        """Delete a message."""
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        can_delete = msg_row["author_id"] == user_id
        if not can_delete:
            participant = self._participant_svc.get_participant(
                msg_row["conversation_id"], user_id
            )
            if participant and participant.role in [
                ParticipantRole.OWNER,
                ParticipantRole.ADMIN,
            ]:
                can_delete = True

        if not can_delete:
            raise MessageAccessDeniedError("Cannot delete this message")

        now = self._get_timestamp()
        if hard_delete:
            self._repo.hard_delete(message_id)
        else:
            self._repo.soft_delete(message_id, now)

        try:
            search_remove_from_index(message_id)
        except Exception as e:
            logger.debug(
                f"Failed to remove message {message_id} from search index: {e}"
            )

        cache_delete(f"msg:obj:{message_id}")

        try:
            from src.core.database import get_redis_client as get_client

            client = get_client()
            if client:
                client.delete(f"recent_messages:{msg_row['conversation_id']}")
        except Exception:
            pass

        return True

    def delete_messages_bulk(
        self,
        user_id: SnowflakeID,
        message_ids: List[SnowflakeID],
        hard_delete: bool = False,
    ) -> Dict[str, Any]:
        """Delete multiple messages in bulk."""
        success_count = 0
        failed_ids: List[SnowflakeID] = []
        now = self._get_timestamp()

        for message_id in message_ids:
            try:
                msg_row = self._repo.get_by_id(message_id)
                if not msg_row:
                    failed_ids.append(message_id)
                    continue

                can_delete = msg_row["author_id"] == user_id
                if not can_delete:
                    participant = self._participant_svc.get_participant(
                        msg_row["conversation_id"], user_id
                    )
                    if participant and participant.role in [
                        ParticipantRole.OWNER,
                        ParticipantRole.ADMIN,
                    ]:
                        can_delete = True

                if not can_delete:
                    failed_ids.append(message_id)
                    continue

                if hard_delete:
                    self._repo.hard_delete(message_id)
                else:
                    self._repo.soft_delete(message_id, now)

                cache_delete(f"msg:obj:{message_id}")

                try:
                    from src.core import search as search_module

                    search_module.remove_from_index(int(message_id))
                except Exception:
                    pass

                success_count += 1
            except Exception as e:
                logger.error(f"Failed to delete message {message_id}: {e}")
                failed_ids.append(message_id)

        return {
            "success_count": success_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }

    def archive_messages_bulk(
        self,
        user_id: SnowflakeID,
        message_ids: List[SnowflakeID],
    ) -> Dict[str, Any]:
        """Archive multiple messages."""
        import json

        success_count = 0
        failed_ids: List[SnowflakeID] = []
        now = self._get_timestamp()

        for message_id in message_ids:
            try:
                msg_row = self._repo.get_by_id(message_id)
                if not msg_row:
                    failed_ids.append(message_id)
                    continue

                can_archive = False
                participant = self._participant_svc.get_participant(
                    msg_row["conversation_id"], user_id
                )
                if participant and participant.role in [
                    ParticipantRole.OWNER,
                    ParticipantRole.ADMIN,
                ]:
                    can_archive = True

                if not can_archive:
                    failed_ids.append(message_id)
                    continue

                metadata: Dict[str, Any] = {}
                if msg_row.get("metadata"):
                    try:
                        metadata = json.loads(msg_row["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass

                metadata["archived"] = True
                metadata["archived_by"] = user_id
                metadata["archived_at"] = now

                self._repo.update_metadata(message_id, metadata, now)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to archive message {message_id}: {e}")
                failed_ids.append(message_id)

        return {
            "success_count": success_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }
