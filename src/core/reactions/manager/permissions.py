from typing import Optional, List, Dict, Set
import json
import utils.logger as logger
from src.core.base import SnowflakeID


from .protocol import ReactionProtocol


class ReactionPermissionsMixin(ReactionProtocol):
    def get_conversation_id_from_message(
        self, message_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        row = self._db.fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ?", (message_id,)
        )
        return row["conversation_id"] if row else None

    def resolve_message_channel(
        self, message_id: SnowflakeID
    ) -> Optional[Dict[str, Optional[SnowflakeID]]]:
        """Resolve the conversation and real channel id for a message.

        Returns ``None`` when the message does not exist. ``channel_id`` is
        the srv_channels id for server channels, otherwise the conversation
        id itself (DM/group conversations have no channel row).
        """
        conversation_id = self.get_conversation_id_from_message(message_id)
        if not conversation_id:
            return None
        channel = self._get_channel_for_conversation(conversation_id)
        channel_id = channel["id"] if channel else None
        return {
            "conversation_id": conversation_id,
            "channel_id": channel_id or conversation_id,
            "is_server_channel": channel is not None,
        }

    def get_participant_ids(self, conversation_id: SnowflakeID) -> List[SnowflakeID]:
        participant_rows = self._db.fetch_all(
            "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
            (conversation_id,),
        )
        participant_ids = [row["user_id"] for row in participant_rows]

        conv_row = self._db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?", (conversation_id,)
        )
        if conv_row and conv_row.get("metadata"):
            try:
                metadata = (
                    json.loads(conv_row["metadata"])
                    if isinstance(conv_row["metadata"], str)
                    else conv_row["metadata"]
                )
                server_id = (
                    metadata.get("server_id") if isinstance(metadata, dict) else None
                )
                if server_id:
                    channel = self._get_channel_for_conversation(conversation_id)
                    channel_id = channel["id"] if channel else None
                    member_rows = self._db.fetch_all(
                        "SELECT user_id FROM srv_members WHERE server_id = ?",
                        (server_id,),
                    )
                    for row in member_rows:
                        member_id = row["user_id"]
                        if member_id in participant_ids:
                            continue
                        # Only fan out to members who can actually view the
                        # channel; large servers otherwise broadcast every
                        # reaction to thousands of invisible recipients.
                        if self._servers and not self._servers.has_permission(
                            member_id, server_id, "messages.read", channel_id
                        ):
                            continue
                        participant_ids.append(member_id)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(
                    f"Failed to parse conversation metadata for reactions: {e}"
                )

        return participant_ids

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        if row:
            return True

        conv_row = self._db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?", (conversation_id,)
        )
        if conv_row:
            metadata_str = (
                conv_row["metadata"] if "metadata" in conv_row.keys() else None
            )
            if metadata_str:
                try:
                    metadata = (
                        json.loads(metadata_str)
                        if isinstance(metadata_str, str)
                        else metadata_str
                    )
                    server_id = (
                        metadata.get("server_id")
                        if isinstance(metadata, dict)
                        else None
                    )
                    if server_id:
                        member_row = self._db.fetch_one(
                            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
                            (server_id, user_id),
                        )
                        return member_row is not None
                except (json.JSONDecodeError, TypeError):
                    pass

        return False

    def _get_channel_for_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict]:
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE conversation_id = ?", (conversation_id,)
        )

    def _check_server_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
        permission: str = "messages.add_reactions",
    ) -> bool:
        # Fail CLOSED: if the servers module is not wired we cannot verify
        # the user's permissions, so server-channel actions are denied.
        if not self._servers:
            logger.warning(
                f"Servers module unavailable; denying server permission "
                f"'{permission}' for user {user_id} in server {server_id}"
            )
            return False
        return self._servers.has_permission(user_id, server_id, permission, channel_id)

    def _is_blocked_by_either(
        self, user_id: SnowflakeID, other_id: SnowflakeID
    ) -> bool:
        if not self._relationships:
            return False
        return self._relationships.is_blocked_by_either(user_id, other_id)

    def _get_blocked_user_ids(self, viewer_id: Optional[SnowflakeID]) -> Set[int]:
        """Collect every user id whose reactions ``viewer_id`` must not see."""
        blocked: Set[int] = set()
        if not viewer_id or not self._relationships:
            return blocked
        blocked.update(self._relationships.get_blocked_user_ids(viewer_id))
        rows = self._db.fetch_all(
            "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (viewer_id,)
        )
        for row in rows:
            blocked.add(row["blocker_id"])
        return blocked
