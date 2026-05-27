"""
Thread manager - Core business logic for thread operations.

Handles thread creation, membership, messages, and state management
with proper validation, permission checks, and database interactions.
"""

from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager

from ..models import (
    Thread,
    ThreadMember,
    ThreadType,
    ThreadState,
    AutoArchiveDuration,
)
from ..exceptions import (
    ThreadError,
    ThreadNotFoundError,
    ThreadAccessDeniedError,
    ThreadLockedError,
    ThreadMemberNotFoundError,
    ThreadMemberExistsError,
    ThreadNameError,
    MessageNotFoundError,
    ChannelNotFoundError,
    PermissionDeniedError,
)


class ThreadManager(BaseManager):
    """Core thread manager handling all operations."""

    MAX_THREAD_NAME_LENGTH = 100

    def __init__(
        self,
        db,
        auth_module=None,
        messaging_module=None,
        servers_module=None,
        notifications_module=None,
    ):
        """
        Initialize the thread manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            messaging_module: Optional messaging module for thread messages
            servers_module: Optional servers module for channel/permission checks
            notifications_module: Optional notifications module for thread mentions
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._notifications = notifications_module
        self._encrypt_thread_names = config.get(
            "encryption.encrypt_thread_names", False
        )

        logger.info("Threads module initialized")

    def _validate_thread_name(self, name: str) -> str:
        """Validate and sanitize thread name."""
        if not name or not name.strip():
            raise ThreadNameError("Thread name cannot be empty")

        name = name.strip()
        if len(name) > self.MAX_THREAD_NAME_LENGTH:
            raise ThreadNameError(
                f"Thread name cannot exceed {self.MAX_THREAD_NAME_LENGTH} characters",
                name,
            )

        return name

    def _get_channel(self, channel_id: int) -> Optional[Dict[str, Any]]:
        """Get channel info from database."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE id = ? AND deleted = 0", (channel_id,)
        )
        return dict(row) if row else None

    def _get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Get message info from database."""
        row = self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
        )
        return dict(row) if row else None

    def _check_permission(
        self,
        user_id: int,
        server_id: int,
        permission: str,
        channel_id: Optional[int] = None,
    ) -> bool:
        """Check if user has permission."""
        if self._servers:
            return self._servers.has_permission(
                user_id, server_id, permission, channel_id
            )
        return True

    def _require_permission(
        self,
        user_id: int,
        server_id: int,
        permission: str,
        channel_id: Optional[int] = None,
    ) -> None:
        """Require a permission, raising if not granted."""
        if not self._check_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(f"Missing permission: {permission}", permission)

    def _row_to_thread(self, row: Dict[str, Any]) -> Thread:
        """Convert database row to Thread."""
        # Decrypt thread name if encryption is enabled and encrypted data exists
        name = row["name"]
        if self._encrypt_thread_names and row.get("name_encrypted"):
            from src.utils.encryption import decrypt_data

            try:
                name = decrypt_data(row["name_encrypted"])
            except Exception as e:
                logger.warning(f"Failed to decrypt thread name {row['id']}: {e}")
                name = row["name"]  # Fallback to unencrypted

        return Thread(
            id=row["id"],
            channel_id=row["channel_id"],
            server_id=row["server_id"],
            owner_id=row["owner_id"],
            name=name,
            thread_type=ThreadType(row["thread_type"]),
            state=ThreadState(row["state"]),
            parent_message_id=row["parent_message_id"],
            auto_archive_duration=AutoArchiveDuration(row["auto_archive_duration"]),
            message_count=row["message_count"],
            member_count=row["member_count"],
            created_at=row["created_at"],
            archived_at=row["archived_at"],
            last_message_at=row["last_message_at"],
            conversation_id=row.get("conversation_id"),
            locked=bool(row["locked"]),
        )

    def _row_to_thread_member(self, row: Dict[str, Any]) -> ThreadMember:
        """Convert database row to ThreadMember."""
        return ThreadMember(
            thread_id=row["thread_id"],
            user_id=row["user_id"],
            joined_at=row["joined_at"],
            last_read_message_id=row["last_read_message_id"],
            muted=bool(row["muted"]),
        )

    def _check_auto_archive(self, thread: Thread) -> bool:
        """Check if thread should be auto-archived based on inactivity."""
        if thread.state != ThreadState.ACTIVE:
            return False

        now = self._get_timestamp()
        last_activity = thread.last_message_at or thread.created_at
        duration_ms = thread.auto_archive_duration.value * 60 * 1000

        return (now - last_activity) > duration_ms

    def _is_member(self, user_id: int, thread_id: int) -> bool:
        """Check if user is a member of the thread."""
        row = self._db.fetch_one(
            "SELECT 1 FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        return row is not None

    def _update_member_count(self, thread_id: int) -> None:
        """Update the member count for a thread."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM thread_members WHERE thread_id = ?",
            (thread_id,),
        )
        count = row["count"] if row else 0
        self._db.execute(
            "UPDATE thread_threads SET member_count = ? WHERE id = ?",
            (count, thread_id),
        )

    # === Thread Creation ===

    def create_thread(
        self,
        user_id: int,
        channel_id: int,
        name: str,
        thread_type: ThreadType = ThreadType.PUBLIC,
        auto_archive_duration: AutoArchiveDuration = AutoArchiveDuration.ONE_DAY,
    ) -> Thread:
        """
        Create a new thread in a channel (forum-style, no parent message).

        Args:
            user_id: ID of the user creating the thread
            channel_id: ID of the parent channel
            name: Thread name (max 100 chars)
            thread_type: Type of thread (public, private, announcement)
            auto_archive_duration: Duration before auto-archive

        Returns:
            Created Thread
        """
        name = self._validate_thread_name(name)

        channel = self._get_channel(channel_id)
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

        # Encrypt thread name if enabled
        name_encrypted = None
        if self._encrypt_thread_names:
            from src.utils.encryption import encrypt_data

            name_encrypted = encrypt_data(name)

        # Create conversation for the thread
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
        assert result is not None  # Should exist since we just created/updated it
        return result

    def create_thread_from_message(
        self,
        user_id: int,
        message_id: int,
        name: str,
        thread_type: ThreadType = ThreadType.PUBLIC,
        auto_archive_duration: AutoArchiveDuration = AutoArchiveDuration.ONE_DAY,
    ) -> Thread:
        """
        Create a thread from an existing message (reply thread).

        Args:
            user_id: ID of the user creating the thread
            message_id: ID of the parent message
            name: Thread name (max 100 chars)
            thread_type: Type of thread (public, private, announcement)
            auto_archive_duration: Duration before auto-archive

        Returns:
            Created Thread
        """
        name = self._validate_thread_name(name)

        message = self._get_message(message_id)
        if not message:
            raise MessageNotFoundError(f"Message {message_id} not found")

        existing = self._db.fetch_one(
            "SELECT id FROM thread_threads WHERE parent_message_id = ? AND deleted = 0",
            (message_id,),
        )
        if existing:
            raise ThreadError("A thread already exists for this message")

        conversation_id = message["conversation_id"]
        # Get channel_id from conversation metadata or msg_participants
        channel_id = None
        # Try to get channel_id from conversation metadata
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

        # If not in metadata, try to get from msg_messages (which might have channel_id)
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

        # Encrypt thread name if enabled
        name_encrypted = None
        if self._encrypt_thread_names:
            from src.utils.encryption import encrypt_data

            name_encrypted = encrypt_data(name)

        # Create conversation for the thread
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
        assert result is not None  # Should exist since we just created/updated it
        return result

    # === Thread Membership ===

    def join_thread(self, user_id: int, thread_id: int) -> ThreadMember:
        """
        Join a thread.

        Args:
            user_id: ID of the user joining
            thread_id: ID of the thread

        Returns:
            ThreadMember
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_view_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot access this thread")

        if self._is_member(user_id, thread_id):
            raise ThreadMemberExistsError("Already a member of this thread")

        now = self._get_timestamp()
        self._db.execute(
            """INSERT INTO thread_members (thread_id, user_id, joined_at)
               VALUES (?, ?, ?)""",
            (thread_id, user_id, now),
        )

        self._update_member_count(thread_id)

        logger.debug(f"User {user_id} joined thread {thread_id}")

        member = self._get_member(thread_id, user_id)
        assert member is not None
        return member

    def leave_thread(self, user_id: int, thread_id: int) -> bool:
        """
        Leave a thread.

        Args:
            user_id: ID of the user leaving
            thread_id: ID of the thread

        Returns:
            True if successfully left
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self._is_member(user_id, thread_id):
            raise ThreadMemberNotFoundError("Not a member of this thread")

        self._db.execute(
            "DELETE FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )

        self._update_member_count(thread_id)

        logger.debug(f"User {user_id} left thread {thread_id}")

        return True

    def add_member(
        self, user_id: int, thread_id: int, member_user_id: int
    ) -> ThreadMember:
        """
        Add a member to a thread.

        Args:
            user_id: ID of the user adding the member
            thread_id: ID of the thread
            member_user_id: ID of the user to add

        Returns:
            ThreadMember
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self._is_member(user_id, thread_id):
            raise ThreadAccessDeniedError("Must be a member to add others")

        if self._is_member(member_user_id, thread_id):
            raise ThreadMemberExistsError("User is already a member")

        if thread.thread_type == ThreadType.PRIVATE:
            if not self.can_manage_thread(user_id, thread_id):
                raise PermissionDeniedError(
                    "Cannot add members to private thread", "threads.manage"
                )

        now = self._get_timestamp()
        self._db.execute(
            """INSERT INTO thread_members (thread_id, user_id, joined_at)
               VALUES (?, ?, ?)""",
            (thread_id, member_user_id, now),
        )

        self._update_member_count(thread_id)

        logger.debug(f"User {member_user_id} added to thread {thread_id} by {user_id}")

        member = self._get_member(thread_id, member_user_id)
        assert member is not None
        return member

    def remove_member(self, user_id: int, thread_id: int, member_user_id: int) -> bool:
        """
        Remove a member from a thread.

        Args:
            user_id: ID of the user removing the member
            thread_id: ID of the thread
            member_user_id: ID of the user to remove

        Returns:
            True if successfully removed
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self._is_member(member_user_id, thread_id):
            raise ThreadMemberNotFoundError("User is not a member")

        if user_id != member_user_id:
            if not self.can_manage_thread(user_id, thread_id):
                raise PermissionDeniedError("Cannot remove members", "threads.manage")

        if member_user_id == thread.owner_id and user_id != thread.owner_id:
            raise PermissionDeniedError("Cannot remove thread owner", "threads.manage")

        self._db.execute(
            "DELETE FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, member_user_id),
        )

        self._update_member_count(thread_id)

        logger.debug(
            f"User {member_user_id} removed from thread {thread_id} by {user_id}"
        )

        return True

    def get_thread_members(
        self,
        user_id: int,
        thread_id: int,
        limit: int = 100,
        after_user_id: Optional[int] = None,
    ) -> List[ThreadMember]:
        """
        Get members of a thread.

        Args:
            user_id: ID of the requesting user
            thread_id: ID of the thread
            limit: Maximum number of members to return
            after_user_id: Pagination cursor

        Returns:
            List of ThreadMember
        """
        if not self.can_view_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot access this thread")

        if after_user_id:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_members 
                   WHERE thread_id = ? AND user_id > ?
                   ORDER BY user_id LIMIT ?""",
                (thread_id, after_user_id, limit),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_members 
                   WHERE thread_id = ?
                   ORDER BY user_id LIMIT ?""",
                (thread_id, limit),
            )

        return [self._row_to_thread_member(row) for row in rows]

    def _get_member(self, thread_id: int, user_id: int) -> Optional[ThreadMember]:
        """Get a specific thread member."""
        row = self._db.fetch_one(
            "SELECT * FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        return self._row_to_thread_member(row) if row else None

    # === Thread Messages ===

    def send_message(
        self,
        user_id: int,
        thread_id: int,
        content: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """
        Send a message to a thread.

        Args:
            user_id: ID of the user sending
            thread_id: ID of the thread
            content: Message content
            attachments: Optional list of attachments

        Returns:
            Message object from messaging module
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        # Check locked status first to give more specific error
        if thread.locked:
            # Only users who can manage the thread can send to locked threads
            if not self.can_manage_thread(user_id, thread_id):
                raise ThreadLockedError("Thread is locked")

        if not self.can_send_in_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot send messages in this thread")

        if thread.state == ThreadState.ARCHIVED:
            self._unarchive_thread_internal(thread_id)

        if not self._is_member(user_id, thread_id):
            self.join_thread(user_id, thread_id)

        now = self._get_timestamp()
        message_id = self._generate_id()

        # Send actual message via messaging module if conversation exists
        if self._messaging and thread.conversation_id:
            try:
                # messaging.send_message handles its own DB transaction
                msg = self._messaging.send_message(
                    user_id=user_id,
                    conversation_id=thread.conversation_id,
                    content=content,
                    attachments=attachments,
                )
                message_id = msg.id
            except Exception as e:
                logger.error(
                    f"Failed to send message via messaging module for thread {thread_id}: {e}"
                )
                # Fallback to local message ID if messaging fails (though content might be lost)

        self._db.execute(
            """INSERT INTO thread_messages (id, thread_id, message_id, user_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (self._generate_id(), thread_id, message_id, user_id, now),
        )

        self._db.execute(
            """UPDATE thread_threads 
               SET message_count = message_count + 1, last_message_at = ?
               WHERE id = ?""",
            (now, thread_id),
        )

        logger.debug(f"Message sent to thread {thread_id} by user {user_id}")

        return {
            "id": message_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "content": content,
            "created_at": now,
        }

    def get_messages(
        self,
        user_id: int,
        thread_id: int,
        limit: int = 50,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None,
    ) -> List[Any]:
        """
        Get messages from a thread.

        Args:
            user_id: ID of the requesting user
            thread_id: ID of the thread
            limit: Maximum number of messages
            before_id: Get messages before this ID
            after_id: Get messages after this ID

        Returns:
            List of messages with content
        """
        if not self.can_view_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot access this thread")

        # Join with msg_messages to get actual content
        query = """
            SELECT tm.*, m.content, m.content_encrypted, m.author_id, m.created_at as message_created_at
            FROM thread_messages tm
            JOIN msg_messages m ON tm.message_id = m.id
            WHERE tm.thread_id = ?
        """
        params: List[Any] = [thread_id]

        if before_id:
            query += " AND tm.message_id < ?"
            params.append(before_id)
            query += " ORDER BY tm.message_id DESC LIMIT ?"
        elif after_id:
            query += " AND tm.message_id > ?"
            params.append(after_id)
            query += " ORDER BY tm.message_id ASC LIMIT ?"
        else:
            query += " ORDER BY tm.message_id DESC LIMIT ?"

        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))

        # Handle message decryption if needed
        from src.utils.encryption import decrypt_message

        results = []
        for row in rows:
            data = dict(row)
            content = data.get("content", "")

            # Use decrypt_message helper which handles prefix detection
            try:
                data["content"] = decrypt_message(content, data["message_id"])
            except Exception as e:
                # If decryption fails, keep as is (might be plaintext or corrupted)
                logger.debug(
                    f"Failed to decrypt message {data['message_id']} in thread {thread_id}: {e}"
                )

            results.append(data)

        return results

    def get_message_count(self, thread_id: int) -> int:
        """Get the message count for a thread."""
        row = self._db.fetch_one(
            "SELECT message_count FROM thread_threads WHERE id = ? AND deleted = 0",
            (thread_id,),
        )
        return row["message_count"] if row else 0

    # === Thread State ===

    def archive_thread(self, user_id: int, thread_id: int) -> Thread:
        """
        Archive a thread.

        Args:
            user_id: ID of the user archiving
            thread_id: ID of the thread

        Returns:
            Updated Thread
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot archive thread", "threads.manage")

        if thread.state == ThreadState.ARCHIVED:
            return thread

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE thread_threads SET state = ?, archived_at = ? WHERE id = ?",
            (ThreadState.ARCHIVED.value, now, thread_id),
        )

        logger.debug(f"Thread {thread_id} archived by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def _unarchive_thread_internal(self, thread_id: int) -> None:
        """Internal method to unarchive a thread."""
        self._db.execute(
            "UPDATE thread_threads SET state = ?, archived_at = NULL WHERE id = ?",
            (ThreadState.ACTIVE.value, thread_id),
        )

    def unarchive_thread(self, user_id: int, thread_id: int) -> Thread:
        """
        Unarchive a thread.

        Args:
            user_id: ID of the user unarchiving
            thread_id: ID of the thread

        Returns:
            Updated Thread
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            if not self._is_member(user_id, thread_id):
                raise PermissionDeniedError("Cannot unarchive thread", "threads.manage")

        if thread.state != ThreadState.ARCHIVED:
            return thread

        self._unarchive_thread_internal(thread_id)

        logger.debug(f"Thread {thread_id} unarchived by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def lock_thread(self, user_id: int, thread_id: int) -> Thread:
        """
        Lock a thread (prevent new messages).

        Args:
            user_id: ID of the user locking
            thread_id: ID of the thread

        Returns:
            Updated Thread
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot lock thread", "threads.manage")

        self._db.execute(
            "UPDATE thread_threads SET locked = 1 WHERE id = ?", (thread_id,)
        )

        logger.debug(f"Thread {thread_id} locked by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def unlock_thread(self, user_id: int, thread_id: int) -> Thread:
        """
        Unlock a thread.

        Args:
            user_id: ID of the user unlocking
            thread_id: ID of the thread

        Returns:
            Updated Thread
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot unlock thread", "threads.manage")

        self._db.execute(
            "UPDATE thread_threads SET locked = 0 WHERE id = ?", (thread_id,)
        )

        logger.debug(f"Thread {thread_id} unlocked by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    # === Thread Listing ===

    def get_active_threads(self, user_id: int, channel_id: int) -> List[Thread]:
        """
        Get active threads in a channel.

        Args:
            user_id: ID of the requesting user
            channel_id: ID of the channel

        Returns:
            List of active threads
        """
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        rows = self._db.fetch_all(
            """SELECT * FROM thread_threads 
               WHERE channel_id = ? AND state = ? AND deleted = 0
               ORDER BY last_message_at DESC NULLS LAST""",
            (channel_id, ThreadState.ACTIVE.value),
        )

        # SECURITY: Bulk check view permissions to avoid N+1 queries
        threads = []
        possible_threads = [self._row_to_thread(row) for row in rows]

        # Filter out auto-archived threads first
        active_threads = []
        for thread in possible_threads:
            if self._check_auto_archive(thread):
                self._db.execute(
                    "UPDATE thread_threads SET state = ?, archived_at = ? WHERE id = ?",
                    (ThreadState.ARCHIVED.value, self._get_timestamp(), thread.id),
                )
                continue
            active_threads.append(thread)

        if not active_threads:
            return []

        # Check membership for private threads in bulk
        private_thread_ids = {
            t.id for t in active_threads if t.thread_type == ThreadType.PRIVATE
        }
        member_thread_ids = set()
        if private_thread_ids:
            # Validate thread IDs are integers to prevent SQL injection
            if not all(isinstance(tid, int) for tid in private_thread_ids):
                raise ValueError("All thread IDs must be integers")
            # Avoid dynamic IN clause to satisfy bandit - use individual queries
            member_thread_ids = set()
            for thread_id in private_thread_ids:
                row = self._db.fetch_one(
                    "SELECT thread_id FROM thread_members WHERE user_id = ? AND thread_id = ?",
                    (user_id, thread_id),
                )
                if row:
                    member_thread_ids.add(row["thread_id"])

        # Check server permission once for public threads (they all share the same channel/server)
        has_server_view = True
        public_exists = any(t.thread_type != ThreadType.PRIVATE for t in active_threads)
        if public_exists and self._servers:
            first_public = next(
                t for t in active_threads if t.thread_type != ThreadType.PRIVATE
            )
            has_server_view = self._check_permission(
                user_id,
                first_public.server_id,
                "channels.view",
                first_public.channel_id,
            )

        for thread in active_threads:
            if thread.thread_type == ThreadType.PRIVATE:
                if thread.id in member_thread_ids:
                    threads.append(thread)
            elif has_server_view:
                threads.append(thread)

        return threads

    def get_archived_threads(
        self,
        user_id: int,
        channel_id: int,
        limit: int = 50,
        before_timestamp: Optional[int] = None,
    ) -> List[Thread]:
        """
        Get archived threads in a channel (paginated).

        Args:
            user_id: ID of the requesting user
            channel_id: ID of the channel
            limit: Maximum number of threads
            before_timestamp: Get threads archived before this timestamp

        Returns:
            List of archived threads
        """
        channel = self._get_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if before_timestamp:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_threads 
                   WHERE channel_id = ? AND state = ? AND deleted = 0 AND archived_at < ?
                   ORDER BY archived_at DESC LIMIT ?""",
                (channel_id, ThreadState.ARCHIVED.value, before_timestamp, limit),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_threads 
                   WHERE channel_id = ? AND state = ? AND deleted = 0
                   ORDER BY archived_at DESC LIMIT ?""",
                (channel_id, ThreadState.ARCHIVED.value, limit),
            )

        # SECURITY: Bulk check view permissions to avoid N+1 queries
        archived_threads = [self._row_to_thread(row) for row in rows]
        if not archived_threads:
            return []

        # Bulk check permissions
        private_thread_ids = {
            t.id for t in archived_threads if t.thread_type == ThreadType.PRIVATE
        }
        member_thread_ids = set()
        if private_thread_ids:
            # Validate thread IDs are integers to prevent SQL injection
            if not all(isinstance(tid, int) for tid in private_thread_ids):
                raise ValueError("All thread IDs must be integers")
            # Avoid dynamic IN clause to satisfy bandit - use individual queries
            member_thread_ids = set()
            for thread_id in private_thread_ids:
                row = self._db.fetch_one(
                    "SELECT thread_id FROM thread_members WHERE user_id = ? AND thread_id = ?",
                    (user_id, thread_id),
                )
                if row:
                    member_thread_ids.add(row["thread_id"])

        has_server_view = True
        public_exists = any(
            t.thread_type != ThreadType.PRIVATE for t in archived_threads
        )
        if public_exists and self._servers:
            first_public = next(
                t for t in archived_threads if t.thread_type != ThreadType.PRIVATE
            )
            has_server_view = self._check_permission(
                user_id,
                first_public.server_id,
                "channels.view",
                first_public.channel_id,
            )

        threads = []
        for thread in archived_threads:
            if thread.thread_type == ThreadType.PRIVATE:
                if thread.id in member_thread_ids:
                    threads.append(thread)
            elif has_server_view:
                threads.append(thread)

        return threads

    def get_user_threads(
        self, user_id: int, include_archived: bool = False
    ) -> List[Thread]:
        """
        Get threads the user has joined.

        Args:
            user_id: ID of the user
            include_archived: Whether to include archived threads

        Returns:
            List of threads
        """
        if include_archived:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id,),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.state = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadState.ACTIVE.value),
            )

        return [self._row_to_thread(row) for row in rows]

    def get_user_private_threads(
        self, user_id: int, channel_id: Optional[int] = None
    ) -> List[Thread]:
        """
        Get private threads the user is a member of.

        Args:
            user_id: ID of the user
            channel_id: Optional channel filter

        Returns:
            List of private threads
        """
        if channel_id:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.thread_type = ? AND t.channel_id = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadType.PRIVATE.value, channel_id),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.thread_type = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadType.PRIVATE.value),
            )

        return [self._row_to_thread(row) for row in rows]

    # === Thread Info ===

    def _get_thread_internal(self, thread_id: int) -> Optional[Thread]:
        """Get thread without permission check."""
        row = self._db.fetch_one(
            "SELECT * FROM thread_threads WHERE id = ? AND deleted = 0", (thread_id,)
        )
        return self._row_to_thread(row) if row else None

    def get_thread(self, user_id: int, thread_id: int) -> Optional[Thread]:
        """
        Get thread information.

        Args:
            user_id: ID of the requesting user
            thread_id: ID of the thread

        Returns:
            Thread or None
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            return None

        if not self.can_view_thread(user_id, thread_id):
            return None

        if self._check_auto_archive(thread):
            self._db.execute(
                "UPDATE thread_threads SET state = ?, archived_at = ? WHERE id = ?",
                (ThreadState.ARCHIVED.value, self._get_timestamp(), thread_id),
            )
            return self._get_thread_internal(thread_id)

        return thread

    def update_thread(
        self,
        user_id: int,
        thread_id: int,
        name: Optional[str] = None,
        auto_archive_duration: Optional[AutoArchiveDuration] = None,
    ) -> Thread:
        """
        Update thread settings.

        Args:
            user_id: ID of the user updating
            thread_id: ID of the thread
            name: New thread name
            auto_archive_duration: New auto-archive duration

        Returns:
            Updated Thread
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot update thread", "threads.manage")

        updates = []
        params = []

        if name is not None:
            name = self._validate_thread_name(name)
            # Encrypt name if enabled
            name_encrypted = None
            if name and self._encrypt_thread_names:
                from src.utils.encryption import encrypt_data

                name_encrypted = encrypt_data(name)

            updates.append("name = ?")
            params.append(name)

            if name_encrypted:
                updates.append("name_encrypted = ?")
                params.append(name_encrypted)

        if auto_archive_duration is not None:
            updates.append("auto_archive_duration = ?")
            params.append(auto_archive_duration.value)

        if updates:
            params.append(thread_id)
            # Whitelist of allowed column names for UPDATE
            allowed_columns = {"name", "auto_archive_duration"}
            # Validate all column names in updates
            for update in updates:
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")

            # Avoid dynamic UPDATE to satisfy bandit - use if-else for each possible column
            for update in updates:
                if "name = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE thread_threads SET name = ? WHERE id = ?",
                        (params[idx], thread_id),
                    )
                elif "auto_archive_duration = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE thread_threads SET auto_archive_duration = ? WHERE id = ?",
                        (params[idx], thread_id),
                    )

        logger.debug(f"Thread {thread_id} updated by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def delete_thread(self, user_id: int, thread_id: int) -> bool:
        """
        Delete a thread.

        Args:
            user_id: ID of the user deleting
            thread_id: ID of the thread

        Returns:
            True if deleted
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot delete thread", "threads.manage")

        self._db.execute(
            "UPDATE thread_threads SET deleted = 1 WHERE id = ?", (thread_id,)
        )

        logger.debug(f"Thread {thread_id} deleted by user {user_id}")

        return True

    # === Permission Checks ===

    def can_view_thread(self, user_id: int, thread_id: int) -> bool:
        """
        Check if user can view a thread.

        Args:
            user_id: ID of the user
            thread_id: ID of the thread

        Returns:
            True if user can view
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            return False

        if thread.thread_type == ThreadType.PRIVATE:
            return self._is_member(user_id, thread_id)

        if self._servers:
            return self._check_permission(
                user_id, thread.server_id, "channels.view", thread.channel_id
            )

        return True

    def can_send_in_thread(self, user_id: int, thread_id: int) -> bool:
        """
        Check if user can send messages in a thread.

        Args:
            user_id: ID of the user
            thread_id: ID of the thread

        Returns:
            True if user can send
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            return False

        if thread.locked:
            return self.can_manage_thread(user_id, thread_id)

        if not self.can_view_thread(user_id, thread_id):
            return False

        if self._servers:
            return self._check_permission(
                user_id, thread.server_id, "messages.send", thread.channel_id
            )

        return True

    def can_manage_thread(self, user_id: int, thread_id: int) -> bool:
        """
        Check if user can manage a thread (archive, lock, delete).

        Args:
            user_id: ID of the user
            thread_id: ID of the thread

        Returns:
            True if user can manage
        """
        thread = self._get_thread_internal(thread_id)
        if not thread:
            return False

        if thread.owner_id == user_id:
            return True

        if self._servers:
            return self._check_permission(
                user_id, thread.server_id, "threads.manage", thread.channel_id
            )

        return False
