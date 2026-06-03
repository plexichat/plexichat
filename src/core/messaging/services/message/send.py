"""
Send message operations mixin for the MessageService.
"""

import urllib.parse
from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_set
from src.utils.encryption import (
    encrypt_message,
    blind_index,
    encrypt_data,
)

from ...models import Message, MessageType, Attachment, MessageStatusType
from ...exceptions import (
    ConversationAccessDeniedError,
    MessageNotFoundError,
    InvalidContentError,
    ContentTooLongError,
    AttachmentLimitError,
)
from ..base import BaseService
from .protocol import MessageServiceProtocol
from .search_helpers import search_index_message


class SendMixin(BaseService, MessageServiceProtocol):
    """Mixin providing send-related message operations."""

    _ratchet_manager: Any = None

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
    ) -> Message:
        """Send a message to a conversation."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        user_settings = self._user_settings_svc.get_message_settings(user_id)
        max_length = user_settings.max_message_length or self._get_config(
            "max_message_length", 4000
        )

        user_filter = self._content_filter_svc.get_filter_settings(user_id)
        content_result = self._content_filter_svc.validate_content(
            content, user_filter, max_length
        )

        if not content_result.valid:
            if any(
                "exceeds maximum length" in issue for issue in content_result.issues
            ):
                raise ContentTooLongError(
                    f"Message exceeds maximum length of {max_length}",
                    max_length,
                    len(content),
                )
            raise InvalidContentError("Invalid message content", content_result.issues)

        if reply_to_id:
            if not self._repo.exists_in_conversation(reply_to_id, conversation_id):
                raise MessageNotFoundError(
                    "Reply target message not found in this conversation"
                )

        if attachments:
            max_attachments = (
                user_settings.max_attachments_per_message
                or self._get_config("max_attachments_per_message", 10)
            )
            if len(attachments) > max_attachments:
                raise AttachmentLimitError(
                    f"Cannot attach more than {max_attachments} files",
                    max_attachments,
                    len(attachments),
                )

        now = self._get_timestamp()
        msg_id = self._generate_id()

        final_content = content_result.sanitized_content
        content_idx = blind_index(final_content, "message_content")

        ratchet_interval_id = None
        if self._get_config("encrypt_messages", True):
            if self._ratchet_manager is not None:
                ratchet_result = self._ratchet_manager.encrypt(
                    conversation_id=conversation_id,
                    message_id=msg_id,
                    plaintext=final_content.encode("utf-8"),
                    now=now,
                )
                final_content = ratchet_result.envelope
                ratchet_interval_id = ratchet_result.interval_id
            else:
                final_content = encrypt_message(final_content, msg_id)

        metadata: Dict[str, Any] = {}
        if content_result.has_spoilers:
            metadata["has_spoilers"] = True
        if content_result.has_nsfw:
            metadata["has_nsfw"] = True
        if content_result.filtered_words:
            metadata["filtered"] = True
        if embeds:
            metadata["embeds"] = embeds

        try:
            self._repo.begin_transaction()
            self._repo.create(
                msg_id,
                conversation_id,
                user_id,
                final_content,
                message_type,
                now,
                reply_to_id=reply_to_id,
                content_index=content_idx,
                metadata=metadata if metadata else None,
                webhook_id=webhook_id,
                ratchet_interval_id=ratchet_interval_id,
                auto_commit=False,
            )

            self._conversation_repo.update(
                conv_id=conversation_id,
                updated_at=now,
                last_message_id=msg_id,
                last_message_at=now,
                auto_commit=False,
            )

            status_id = self._generate_id()
            self._status_repo.create(
                status_id=status_id,
                message_id=msg_id,
                user_id=user_id,
                status=MessageStatusType.SENT,
                timestamp=now,
                auto_commit=False,
            )

            attachment_list: List[Attachment] = []
            if attachments:
                bulk_data = []
                for att_data in attachments:
                    att_id = self._generate_id()
                    normalized_url = self._normalize_url(att_data.get("url", ""))
                    encrypted_url = None
                    stored_url = normalized_url
                    if self._get_config("encrypt_attachments"):
                        encrypted_url = encrypt_data(normalized_url)
                        stored_url = "[encrypted]"
                    bulk_data.append(
                        {
                            "id": att_id,
                            "message_id": msg_id,
                            "filename": att_data.get("filename", "file"),
                            "content_type": att_data.get(
                                "content_type", "application/octet-stream"
                            ),
                            "size": att_data.get("size", 0),
                            "url": stored_url,
                            "url_encrypted": encrypted_url,
                            "created_at": now,
                            "metadata": att_data.get("metadata"),
                            "checksum": att_data.get("hash")
                            or att_data.get("checksum"),
                        }
                    )
                    attachment_list.append(
                        Attachment(
                            id=att_id,
                            message_id=msg_id,
                            filename=att_data.get("filename", "file"),
                            content_type=att_data.get(
                                "content_type", "application/octet-stream"
                            ),
                            size=att_data.get("size", 0),
                            url=normalized_url,
                            created_at=now,
                            checksum=att_data.get("hash") or att_data.get("checksum"),
                        )
                    )
                self._attachment_repo.create_bulk(bulk_data, auto_commit=False)

            self._repo.commit()
        except Exception:
            self._repo.rollback()
            raise

        logger.debug(f"Message {msg_id} sent to conversation {conversation_id}")

        if self._ratchet_manager is not None and ratchet_interval_id is not None:
            try:
                new_interval = self._ratchet_manager.rotate_if_due(
                    conversation_id=conversation_id,
                    last_message_id=msg_id,
                    now_ms=now,
                )
                if new_interval is not None:
                    try:
                        from src.utils.encryption.channel_ratchet import (
                            notify_ratchet_update,
                        )

                        notify_ratchet_update(
                            conversation_id=int(conversation_id),
                            update_data={
                                "reason": "rotation",
                                "new_interval_id": int(new_interval.interval_id),
                                "at_message_id": int(new_interval.start_message_id),
                                "at": int(now),
                            },
                        )
                    except Exception as e:
                        logger.debug(
                            f"Ratchet rotation broadcast scheduling failed: {e}"
                        )
            except Exception as e:
                logger.debug(f"Ratchet rotation check failed: {e}")

        msg = Message(
            id=msg_id,
            conversation_id=conversation_id,
            author_id=user_id,
            content=content_result.sanitized_content,
            message_type=message_type,
            created_at=now,
            updated_at=now,
            reply_to_id=reply_to_id,
            edited=False,
            deleted=False,
            pinned=False,
            metadata=metadata if metadata else None,
            attachments=attachment_list,
        )

        try:
            search_index_message(
                msg_id,
                content_result.sanitized_content,
                {
                    "author_id": user_id,
                    "conversation_id": conversation_id,
                    "created_at": now,
                    "has_attachments": bool(attachments),
                    "has_embeds": bool(embeds),
                    "source_updated_at": now,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to index message for search: {e}")

        cache_set(f"msg:obj:{msg_id}", msg, ttl=3600)

        try:
            from src.core.database import get_redis_client as get_client

            client = get_client()
            if client:
                list_key = f"msg:recent:{conversation_id}"
                client.lpush(list_key, msg_id)
                client.ltrim(list_key, 0, 99)
                client.expire(list_key, 3600)
        except Exception as e:
            logger.debug(f"Failed to update recent messages cache: {e}")

        return msg

    def _normalize_url(self, url: str) -> str:
        if not url:
            return url
        if url.startswith("http://") or url.startswith("https://"):
            parsed = urllib.parse.urlsplit(url)
            path = parsed.path or ""
            if parsed.query:
                path = f"{path}?{parsed.query}"
            return path or url
        return url

    def send_system_message(
        self,
        conversation_id: SnowflakeID,
        content: str,
        event_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Send a system message."""
        now = self._get_timestamp()
        msg_id = self._generate_id()

        full_metadata: Dict[str, Any] = {"event_type": event_type}
        if metadata:
            full_metadata.update(metadata)

        self._repo.create(
            msg_id,
            conversation_id,
            0,
            content,
            MessageType.SYSTEM,
            now,
            metadata=full_metadata,
        )

        row = self._repo.get_by_id(msg_id)
        if row is None:
            raise MessageNotFoundError("Failed to create system message")
        return self._repo.row_to_model(row)
