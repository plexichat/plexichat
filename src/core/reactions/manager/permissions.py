from typing import Optional, List, Dict
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
                    member_rows = self._db.fetch_all(
                        "SELECT user_id FROM srv_members WHERE server_id = ?",
                        (server_id,),
                    )
                    for row in member_rows:
                        if row["user_id"] not in participant_ids:
                            participant_ids.append(row["user_id"])
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
    ) -> bool:
        if not self._servers:
            return True
        return self._servers.has_permission(
            user_id, server_id, "messages.add_reactions", channel_id
        )

    def _is_blocked_by_either(
        self, user_id: SnowflakeID, other_id: SnowflakeID
    ) -> bool:
        if not self._relationships:
            return False
        return self._relationships.is_blocked_by_either(user_id, other_id)
