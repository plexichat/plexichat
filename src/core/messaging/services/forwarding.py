"""
Message forwarding service - Business logic for forwarding messages.

Handles forwarding messages between conversations/DMs with proper
attribution, content preservation, and permission checks.
"""

import time
from typing import List, Dict, Any, Optional

import utils.logger as logger
from src.utils.encryption import (
    decrypt_data,
    decrypt_message,
    generate_snowflake_id,
    is_message_encrypted,
)


# ``msg_forwarded`` columns in schema order (see
# ``src/core/messaging/schema.py``). ``fetch_one``/``fetch_all`` may return a
# mapping or a bare tuple depending on the database adapter (see
# docs/admin-hardening.md); tuple rows from ``SELECT *`` follow this order.
_FORWARDED_COLUMNS = (
    "id",
    "message_id",
    "original_message_id",
    "original_conversation_id",
    "original_author_id",
    "forwarded_by",
    "original_content",
    "original_created_at",
    "created_at",
)

# Column order of the explicit ``msg_messages`` SELECT in ``forward_message``.
_MESSAGE_COLUMNS = (
    "id",
    "conversation_id",
    "author_id",
    "content",
    "content_encrypted",
    "created_at",
)


def _row_to_dict(row, columns) -> Dict[str, Any]:
    """Normalise a mapping or tuple DB row to a dict."""
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(columns, row))


def _forwarded_row_to_dict(row) -> Dict[str, Any]:
    """Normalise a mapping or tuple ``SELECT *`` row to a dict."""
    return _row_to_dict(row, _FORWARDED_COLUMNS)


def _count_value(row) -> int:
    """Read a single-column ``SELECT COUNT(*)`` row by name or position."""
    if row is None:
        return 0
    if isinstance(row, dict):
        return row["count"]
    return row[0]


class ForwardingService:
    """Service for forwarding messages between conversations."""

    MAX_FORWARDS_PER_MESSAGE = 10
    MAX_FORWARDS_PER_USER_PER_HOUR = 50
    MAX_HISTORY = 50

    def __init__(
        self, db, messaging_module=None, participant_svc=None, servers_mod=None
    ):
        self._db = db
        self._messaging = messaging_module
        self._participant_svc = participant_svc
        self._servers = servers_mod

    def _require_channel_view(self, conversation_id, user_id) -> None:
        """Deny when ``conversation_id`` is backed by a server channel the caller cannot view.

        ``is_participant`` grants every member of a server participation in its
        channel conversations, independent of the per-channel ``channels.view``
        permission. Forwarding *reads and decrypts* the source message content
        and re-sends it into the target conversation, so a member denied view
        on a private channel could otherwise exfiltrate its messages by
        forwarding them into a channel/DM they control (and plant content in a
        channel they cannot view via the target side). Resolve the backing
        server channel membership-agnostically and require a live view grant
        on it, exactly like the message get/pin routes. A conversation with no
        backing channel is a genuine DM/group and needs no further gate.

        Fails closed: if the reverse lookup or the view decision cannot be
        established, the forward is denied rather than silently allowed.
        """
        servers = self._servers
        if not servers:
            # The backing-channel lookup cannot be evaluated without the
            # servers module. A private-channel alias can exist in the DB
            # (srv_channels.conversation_id) independently of that module, so
            # skipping this gate would let a member denied channels.view read
            # and decrypt source content into a conversation they control.
            # Deny instead; a wired store whose reverse lookup finds no backing
            # channel still allows genuine DMs below.
            raise PermissionError("Cannot verify channel view access")
        lookup = getattr(servers, "get_channel_by_conversation", None)
        if not callable(lookup):
            raise PermissionError("Cannot verify channel view access")
        try:
            info = lookup(conversation_id)
        except Exception:
            raise PermissionError("Cannot verify channel view access")
        if info is None:
            return  # DM/group conversation: participation already gated it
        channel_id = info.get("id") if isinstance(info, dict) else None
        if channel_id is None:
            raise PermissionError("Cannot verify channel view access")
        get_channel = getattr(servers, "get_channel", None)
        if not callable(get_channel):
            raise PermissionError("Cannot verify channel view access")
        try:
            if get_channel(int(channel_id), user_id) is None:
                raise PermissionError("Cannot view channel for this conversation")
        except PermissionError:
            raise
        except Exception:
            raise PermissionError("Cannot verify channel view access")

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def _safe_send_via_messaging(
        self, user_id: int, target_conversation_id: int, content: str
    ) -> Optional[Any]:
        """Send the forwarded message via the gated messaging path.

        SECURITY: _safe_send_via_messaging propagates send rejections. A
        rejection by ``send_message`` (``InvalidContentError``,
        ``ConversationAccessDeniedError``, slowmode, blocks, ...) MUST
        propagate: recording a forward after a denied send claimed that a
        live message was delivered into the target conversation with content
        the normal send path had rejected, and fabricated a message id for a
        row that never existed. None is returned only when there is no
        messaging module wired at all (no live send path to gate through).
        """
        if not self._messaging:
            return None
        send = getattr(self._messaging, "send_message", None)
        if send is None or not callable(send):
            return None
        return send(
            user_id=user_id,
            conversation_id=target_conversation_id,
            content=content,
        )

    @staticmethod
    def _extract_message_id(message: Any) -> Optional[int]:
        """Best-effort extraction of the snowflake ID from a Message object."""
        if message is None:
            return None
        for attr in ("id", "message_id", "snowflake_id"):
            value = getattr(message, attr, None)
            if value is not None:
                return value
        if isinstance(message, dict):
            return message.get("id") or message.get("message_id")
        return None

    def forward_message(
        self,
        user_id: int,
        message_id: int,
        target_conversation_id: int,
    ) -> Dict[str, Any]:
        """
        Forward a message to another conversation.

        Args:
            user_id: ID of the user forwarding the message
            message_id: ID of the original message to forward
            target_conversation_id: ID of the conversation to forward to

        Returns:
            Forward record and new message info
        """
        # Get original message
        original_row = self._db.fetch_one(
            "SELECT id, conversation_id, author_id, content, content_encrypted, created_at "
            "FROM msg_messages WHERE id = ? AND deleted = 0",
            (message_id,),
        )
        if not original_row:
            raise ValueError("Original message not found")
        original = _row_to_dict(original_row, _MESSAGE_COLUMNS)

        # Authorization MUST fail closed. These checks used to be guarded by
        # ``if self._participant_svc and ...``: when no participation checker
        # was available (messaging module not initialised, or a manager
        # implementation without ``_participant_svc``), BOTH checks were
        # silently skipped. Any authenticated user could then read and decrypt
        # an arbitrary message and insert a forward record pointing at a
        # conversation they do not belong to. Every other messaging service
        # (pin, edit_history, send, retrieval, ...) denies when the checker is
        # missing, and ``get_forward_history`` in this module already does
        # exactly that. Mirror that policy: no checker => no forward.
        if not self._participant_svc:
            raise PermissionError("Cannot verify conversation participation")

        # Check user can read the original message
        if not self._participant_svc.is_participant(
            original["conversation_id"], user_id
        ):
            raise PermissionError("Cannot read the original message")

        # Check user can write to target conversation
        if not self._participant_svc.is_participant(target_conversation_id, user_id):
            raise PermissionError("Cannot send to the target conversation")

        # Participation alone is not enough for server channels: every member
        # of a server is a participant in its channel conversations even when
        # they lack channels.view. Enforce a live view grant on the channel
        # backing BOTH the source (content is read/decrypted from here) and the
        # target (content is written here) before any forward is recorded.
        self._require_channel_view(original["conversation_id"], user_id)
        self._require_channel_view(target_conversation_id, user_id)

        # Rate limit check
        hour_ago = self._get_timestamp() - (3600 * 1000)
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM msg_forwarded WHERE forwarded_by = ? AND created_at > ?",
            (user_id, hour_ago),
        )
        if count_row and _count_value(count_row) >= self.MAX_FORWARDS_PER_USER_PER_HOUR:
            raise ValueError("Forward rate limit exceeded")

        # Check max forwards for original message
        fwd_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM msg_forwarded WHERE original_message_id = ?",
            (message_id,),
        )
        if fwd_count and _count_value(fwd_count) >= self.MAX_FORWARDS_PER_MESSAGE:
            raise ValueError("Message has been forwarded too many times")

        # Create the forwarded message via the gated messaging path, if one is
        # wired. A rejected send propagates: no forward record is written for
        # a message the send path refused to create.
        forward_id = self._generate_id()
        now = self._get_timestamp()

        # L3 fix: NEVER concatenate the stored content verbatim. Stored
        # content may be an ENC envelope (ciphertext), which previously
        # produced literal-garbage forwards AND duplicated the ciphertext
        # into msg_forwarded.original_content. Instead the original is
        # decrypted to plaintext here and re-sent through
        # messaging.send_message, which encrypts for the DESTINATION
        # conversation with the destination key. Undecryptable content is
        # rejected with a clear error rather than forwarded as garbage.
        original_content = self._decrypt_stored_content(original)
        if original_content is None:
            raise ValueError(
                "Cannot forward this message: its encrypted content could "
                "not be decrypted on this server"
            )

        forward_prefix = (
            f"🔄 Forwarded message (originally from <@{original['author_id']}>)\n"
        )
        forward_content = forward_prefix + original_content

        new_message = self._safe_send_via_messaging(
            user_id, target_conversation_id, forward_content
        )
        new_message_id = self._extract_message_id(new_message) or self._generate_id()

        # Record the forward WITHOUT duplicating the original content:
        # msg_forwarded.original_content stays NULL (the plaintext lives in
        # exactly one place — the source row, still subject to its own
        # encryption-at-rest contract).
        self._db.execute(
            """INSERT INTO msg_forwarded
               (id, message_id, original_message_id, original_conversation_id,
                original_author_id, forwarded_by, original_content, original_created_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                forward_id,
                new_message_id,
                message_id,
                original["conversation_id"],
                original["author_id"],
                user_id,
                None,
                original["created_at"],
                now,
            ),
        )

        logger.debug(
            f"User {user_id} forwarded message {message_id} to conversation {target_conversation_id}"
        )

        return {
            "forward_id": forward_id,
            "original_message_id": message_id,
            "original_author_id": original["author_id"],
            "original_conversation_id": original["conversation_id"],
            "new_message_id": new_message_id,
            "target_conversation_id": target_conversation_id,
            "forwarded_by": user_id,
            "created_at": now,
        }

    @staticmethod
    def _decrypt_stored_content(row: Dict[str, Any]) -> Optional[str]:
        """Decrypt a stored msg_messages content value to plaintext.

        Mirrors MessageRepository.row_to_model: ENC envelopes are
        decrypted with the message id (and conversation id for ratchet
        envelopes); legacy rows fall back to the content_encrypted mirror.
        Returns None when decryption is impossible so callers can reject
        instead of forwarding ciphertext garbage.
        """
        content = row.get("content") or ""
        if is_message_encrypted(content):
            try:
                if content.startswith("ENC:3:"):
                    return decrypt_message(
                        content,
                        row["id"],
                        conversation_id=row.get("conversation_id"),
                    )
                return decrypt_message(content, row["id"])
            except Exception as e:
                logger.warning(
                    "Forward: failed to decrypt original message %s: %s",
                    row.get("id"),
                    e,
                )
                return None
        if row.get("content_encrypted") and content == "[encrypted]":
            try:
                return decrypt_data(row["content_encrypted"])
            except Exception as e:
                logger.warning(
                    "Forward: failed to decrypt legacy content of %s: %s",
                    row.get("id"),
                    e,
                )
                return None
        return content

    def get_forward_history(
        self, message_id: int, requester_id: int
    ) -> List[Dict[str, Any]]:
        """Get forwarding history for a message (participant-only).

        L3 fix: reads now REQUIRE the caller to be a participant of the
        original message's conversation; previously ANY caller could
        enumerate where a message was forwarded (cross-conversation
        disclosure). Fails closed when no participation checker exists.
        """
        original = self._db.fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ?",
            (message_id,),
        )
        if not original:
            raise ValueError("Original message not found")

        conversation_id = (
            original["conversation_id"] if isinstance(original, dict) else original[0]
        )
        if not self._participant_svc or not self._participant_svc.is_participant(
            conversation_id, requester_id
        ):
            raise PermissionError("Cannot view forwarding history for this message")

        rows = self._db.fetch_all(
            "SELECT * FROM msg_forwarded WHERE original_message_id = ? ORDER BY created_at DESC",
            (message_id,),
        )
        return [_forwarded_row_to_dict(row) for row in rows]

    def get_forwards_by_user(
        self, user_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get forwarding history for a user."""
        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump the whole table.
        limit = max(0, min(limit, self.MAX_HISTORY))
        rows = self._db.fetch_all(
            "SELECT * FROM msg_forwarded WHERE forwarded_by = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [_forwarded_row_to_dict(row) for row in rows]
