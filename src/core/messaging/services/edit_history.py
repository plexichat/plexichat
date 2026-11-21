"""
Edit history service - Business logic for message edit history tracking.
"""

from typing import List, Dict, Any, Optional
import time

from .base import BaseService
from ..repositories.edit_history import EditHistoryRepository, _bounded_history_limit
from ..repositories.message import MessageRepository
from ..exceptions import MessageNotFoundError, ConversationAccessDeniedError
from .participant import ParticipantService
from src.core.base import SnowflakeID
from src.utils.encryption import encrypt_data, decrypt_data
import utils.logger as logger


class EditHistoryService(BaseService):
    """Service for message edit history operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
        encryption_enabled: bool = True,
        servers_mod: Any = None,
    ) -> None:
        """Initialize edit history service."""
        super().__init__(db)
        self._repo = EditHistoryRepository(db)
        self._message_repo = MessageRepository(db)
        self._participant_svc = participant_service
        self._encryption_enabled = encryption_enabled
        self._servers = servers_mod

    def _require_channel_view(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        """Deny when ``conversation_id`` is a server channel the caller cannot view.

        ``is_participant`` grants every member of a server participation in its
        channel conversations, independent of the per-channel ``channels.view``
        permission. Edit-history reads decrypt and return *previous message
        content*, so a member denied view on a private channel could otherwise
        read old versions of messages they are not allowed to see (the same
        gap already closed for the message get, pin and forward paths). Resolve
        the backing server channel membership-agnostically and require a live
        view grant on it. A conversation with no backing channel is a genuine
        DM/group and needs no further gate.

        Fails closed: if the reverse lookup or the view decision cannot be
        established, the read is denied rather than silently allowed.
        """
        servers = self._servers
        if not servers:
            # The backing-channel lookup cannot be evaluated without the
            # servers module. A private-channel alias can exist in the DB
            # (srv_channels.conversation_id) independently of that module, so
            # skipping this gate would let a member denied channels.view read
            # previous message content. Deny instead; a wired store whose
            # reverse lookup finds no backing channel still allows genuine
            # DMs below.
            raise ConversationAccessDeniedError("Cannot verify channel view access")
        lookup = getattr(servers, "get_channel_by_conversation", None)
        if not callable(lookup):
            raise ConversationAccessDeniedError("Cannot verify channel view access")
        try:
            info = lookup(conversation_id)
        except Exception:
            raise ConversationAccessDeniedError("Cannot verify channel view access")
        if info is None:
            return  # DM/group conversation: participation already gated it
        channel_id = info.get("id") if isinstance(info, dict) else None
        if channel_id is None:
            raise ConversationAccessDeniedError("Cannot verify channel view access")
        get_channel = getattr(servers, "get_channel", None)
        if not callable(get_channel):
            raise ConversationAccessDeniedError("Cannot verify channel view access")
        try:
            if get_channel(int(channel_id), user_id) is None:
                raise ConversationAccessDeniedError(
                    "Cannot view channel for this conversation"
                )
        except ConversationAccessDeniedError:
            raise
        except Exception:
            raise ConversationAccessDeniedError("Cannot verify channel view access")

    def record_edit(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        old_content: str,
    ) -> bool:
        """
        Record a message edit in history.

        Args:
            user_id: ID of the user editing the message
            message_id: ID of the message being edited
            old_content: Previous content before edit

        Returns:
            True if recorded successfully

        Raises:
            MessageNotFoundError: If message doesn't exist
            ConversationAccessDeniedError: If user not in conversation
        """
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        if msg_row["deleted"]:
            raise MessageNotFoundError("Message not found")

        # Verify user can edit this message (author or moderator)
        if msg_row["author_id"] != user_id:
            # Could add moderator check here
            raise ConversationAccessDeniedError("Cannot edit this message")

        conversation_id = msg_row["conversation_id"]

        # Verify participant
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        # Get current version number
        current_version = self._repo.get_latest_version(message_id) or 0
        new_version = current_version + 1

        # Encrypt content if enabled. When the encrypted blob is produced
        # successfully the plaintext MUST NOT be persisted alongside it:
        # storing both would defeat the encryption-at-rest contract that
        # applies to live message content. The NOT NULL old_content column
        # keeps a non-reversible "[encrypted]" marker instead.
        old_content_encrypted = None
        encryption_required = bool(self._encryption_enabled)
        if encryption_required:
            try:
                old_content_encrypted = encrypt_data(old_content)
            except Exception as e:
                logger.warning(f"Failed to encrypt edit history: {e}")
        # Fail CLOSED on an encryption failure. When encryption is enabled the
        # plaintext MUST NOT be persisted at rest: the previous fallback stored
        # ``old_content`` verbatim whenever ``encrypt_data`` raised, defeating
        # the encryption-at-rest contract for prior message content (which can
        # hold sensitive text) exactly when the keyring is misconfigured. Keep
        # the history row, but substitute a non-reversible redaction marker so
        # no plaintext leaks to the database, backups or a reader of the column.
        if encryption_required and old_content_encrypted is None:
            stored_old_content = "[unavailable]"
        elif old_content_encrypted is not None:
            stored_old_content = "[encrypted]"
        else:
            stored_old_content = old_content

        now = int(time.time() * 1000)
        edit_id = self._generate_id()

        self._repo.create(
            edit_id=edit_id,
            message_id=message_id,
            editor_id=user_id,
            old_content=stored_old_content,
            old_content_encrypted=old_content_encrypted,
            edit_timestamp=now,
            version_number=new_version,
        )

        logger.debug(
            f"Recorded edit {new_version} for message {message_id} by user {user_id}"
        )
        return True

    def get_edit_history(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get edit history for a message.

        The number of returned entries is always bounded: ``limit`` is
        clamped to at most ``MAX_EDIT_HISTORY_ROWS`` and the newest versions
        are retained, so a message with an oversized history cannot exhaust
        memory on read.

        Args:
            user_id: ID of the user requesting history
            message_id: ID of the message
            limit: Optional maximum number of entries (clamped to a safe cap)

        Returns:
            List of edit history entries

        Raises:
            MessageNotFoundError: If message doesn't exist
            ConversationAccessDeniedError: If user not in conversation
        """
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        conversation_id = msg_row["conversation_id"]

        # Verify participant
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        # Participation alone is not enough for server channels: every server
        # member is a participant in its channel conversations even when they
        # lack channels.view. Enforce a live view grant before reading (and
        # decrypting) any prior message content.
        self._require_channel_view(conversation_id, user_id)

        rows = self._repo.get_by_message(
            message_id, limit=_bounded_history_limit(limit)
        )

        # Decrypt if needed
        history = []
        for row in rows:
            entry = {
                "id": row["id"],
                "message_id": row["message_id"],
                "editor_id": row["editor_id"],
                "old_content": row["old_content"],
                "edit_timestamp": row["edit_timestamp"],
                "version_number": row["version_number"],
            }

            # Try to decrypt if encrypted version exists
            if row["old_content_encrypted"] and self._encryption_enabled:
                try:
                    entry["old_content"] = decrypt_data(row["old_content_encrypted"])
                except Exception as e:
                    logger.warning(f"Failed to decrypt edit history: {e}")
                    # Fall back to plain text if decryption fails

            history.append(entry)

        return history

    def get_edit_count(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
    ) -> int:
        """
        Get the number of edits for a message.

        Args:
            user_id: ID of the user requesting count
            message_id: ID of the message

        Returns:
            Number of edits

        Raises:
            MessageNotFoundError: If message doesn't exist
            ConversationAccessDeniedError: If user not in conversation
        """
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        conversation_id = msg_row["conversation_id"]

        # Verify participant
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        # Same live channel-view gate as the history read: an edit count over
        # a private server channel is itself information the caller cannot view.
        self._require_channel_view(conversation_id, user_id)

        return self._repo.count_edits(message_id)
