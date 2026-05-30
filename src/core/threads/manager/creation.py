import utils.logger as logger
from ..models import Thread, ThreadType, ThreadState, AutoArchiveDuration
from ..exceptions import (
    ThreadError,
    ThreadNameError,
    MessageNotFoundError,
    ChannelNotFoundError,
)

from .helpers import _get_channel, _get_message
from .protocol import ThreadProtocol


class ThreadCreationMixin(ThreadProtocol):
    MAX_THREAD_NAME_LENGTH = 100

    def _validate_thread_name(self, name: str) -> str:
        if not name or not name.strip():
            raise ThreadNameError("Thread name cannot be empty")

        name = name.strip()
        if len(name) > self.MAX_THREAD_NAME_LENGTH:
            raise ThreadNameError(
                f"Thread name cannot exceed {self.MAX_THREAD_NAME_LENGTH} characters",
                name,
            )

        return name

    def create_thread(
        self,
        user_id: int,
        channel_id: int,
        name: str,
        thread_type: ThreadType = ThreadType.PUBLIC,
        auto_archive_duration: AutoArchiveDuration = AutoArchiveDuration.ONE_DAY,
    ) -> Thread:
        name = self._validate_thread_name(name)

        channel = _get_channel(self._db, channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        server_id = channel["server_id"]

        if thread_type == ThreadType.PRIVATE:
            self._require_permission(
                user_id, server_id, "threads.create_private", channel_id
            )
        else:
            self._require_permission(
                user_id, server_id, "threads.create_public", channel_id
            )

        now = self._get_timestamp()
        thread_id = self._generate_id()

        name_encrypted = None
        if self._encrypt_thread_names:
            from src.utils.encryption import encrypt_data

            name_encrypted = encrypt_data(name)

        conversation_id = None
        if self._messaging:
            try:
                conv = self._messaging.create_thread_conversation(
                    server_id, channel_id, name
                )
                conversation_id = conv.id
            except Exception as e:
                logger.warning(f"Failed to create conversation for thread: {e}")

        self._db.execute(
            """INSERT INTO thread_threads 
               (id, channel_id, server_id, owner_id, name, name_encrypted, thread_type, state, 
                auto_archive_duration, message_count, member_count, created_at, conversation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                channel_id,
                server_id,
                user_id,
                name,
                name_encrypted,
                thread_type.value,
                ThreadState.ACTIVE.value,
                auto_archive_duration.value,
                0,
                1,
                now,
                conversation_id,
            ),
        )

        self._db.execute(
            """INSERT INTO thread_members (thread_id, user_id, joined_at)
               VALUES (?, ?, ?)""",
            (thread_id, user_id, now),
        )

        logger.debug(
            f"Thread {thread_id} created by user {user_id} in channel {channel_id}"
        )

        result = self.get_thread(user_id, thread_id)
        assert result is not None
        return result

    def create_thread_from_message(
        self,
        user_id: int,
        message_id: int,
        name: str,
        thread_type: ThreadType = ThreadType.PUBLIC,
        auto_archive_duration: AutoArchiveDuration = AutoArchiveDuration.ONE_DAY,
    ) -> Thread:
        name = self._validate_thread_name(name)

        message = _get_message(self._db, message_id)
        if not message:
            raise MessageNotFoundError(f"Message {message_id} not found")

        existing = self._db.fetch_one(
            "SELECT id FROM thread_threads WHERE parent_message_id = ? AND deleted = 0",
            (message_id,),
        )
        if existing:
            raise ThreadError("A thread already exists for this message")

        conversation_id = message["conversation_id"]
        channel_id = None

        conv_row = self._db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?",
            (conversation_id,),
        )
        if conv_row and conv_row.get("metadata"):
            try:
                import json

                metadata = json.loads(conv_row["metadata"])
                channel_id = metadata.get("channel_id")
            except Exception:
                pass

        if not channel_id:
            channel_id = message.get("channel_id")

        if not channel_id:
            raise ThreadError("Cannot determine channel_id for thread creation")

        channel_row = self._db.fetch_one(
            "SELECT server_id FROM srv_channels WHERE id = ?",
            (channel_id,),
        )

        if not channel_row or not channel_row["server_id"]:
            raise ThreadError("Threads can only be created in server channels")

        server_id = channel_row["server_id"]

        if thread_type == ThreadType.PRIVATE:
            self._require_permission(
                user_id, server_id, "threads.create_private", channel_id
            )
        else:
            self._require_permission(
                user_id, server_id, "threads.create_public", channel_id
            )

        now = self._get_timestamp()
        thread_id = self._generate_id()

        name_encrypted = None
        if self._encrypt_thread_names:
            from src.utils.encryption import encrypt_data

            name_encrypted = encrypt_data(name)

        conversation_id = None
        if self._messaging:
            try:
                conv = self._messaging.create_thread_conversation(
                    server_id, channel_id, name
                )
                conversation_id = conv.id
            except Exception as e:
                logger.warning(f"Failed to create conversation for thread: {e}")

        self._db.execute(
            """INSERT INTO thread_threads 
               (id, channel_id, server_id, owner_id, name, name_encrypted, thread_type, state,
                parent_message_id, auto_archive_duration, message_count, member_count, 
                created_at, conversation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                channel_id,
                server_id,
                user_id,
                name,
                name_encrypted,
                thread_type.value,
                ThreadState.ACTIVE.value,
                message_id,
                auto_archive_duration.value,
                0,
                1,
                now,
                conversation_id,
            ),
        )

        self._db.execute(
            """INSERT INTO thread_members (thread_id, user_id, joined_at)
               VALUES (?, ?, ?)""",
            (thread_id, user_id, now),
        )

        logger.debug(
            f"Thread {thread_id} created from message {message_id} by user {user_id}"
        )

        result = self.get_thread(user_id, thread_id)
        assert result is not None
        return result
