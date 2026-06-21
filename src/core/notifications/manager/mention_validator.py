from typing import Optional, List

import utils.config as _mention_config

from ..models import Mention, MentionType, MentionPosition
from ..parser import parse_mentions as _parse_mentions
from src.core.base import SnowflakeID
from .helpers import (
    role_exists,
    get_role,
    channel_exists,
    has_mention_everyone_permission,
)


from .protocol import NotificationProtocol


class MentionValidationMixin(NotificationProtocol):
    def parse_mentions(self, content: str) -> List[Mention]:
        return _parse_mentions(content)

    def validate_mentions(
        self,
        user_id: SnowflakeID,
        mentions: List[Mention],
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
    ) -> List[Mention]:
        validated = []

        for mention in mentions:
            m = Mention(
                mention_type=mention.mention_type,
                target_id=mention.target_id,
                raw_text=mention.raw_text,
                start_pos=mention.start_pos,
                end_pos=mention.end_pos,
                valid=True,
            )

            if mention.mention_type == MentionType.USER:
                if mention.target_id is None or not self._user_exists(
                    mention.target_id
                ):
                    m.valid = False
                    m.error = "User not found"

            elif mention.mention_type == MentionType.ROLE:
                if mention.target_id is None or not role_exists(
                    self._db, mention.target_id
                ):
                    m.valid = False
                    m.error = "Role not found"
                elif server_id and mention.target_id is not None:
                    role = get_role(self._db, mention.target_id)
                    if role and role["server_id"] != server_id:
                        m.valid = False
                        m.error = "Role not in this server"
                    elif role and not bool(role["mentionable"]):
                        if not self._servers or not self._servers.has_permission(
                            user_id, server_id, "roles.manage", channel_id
                        ):
                            m.valid = False
                            m.error = "Role is not mentionable"

            elif mention.mention_type == MentionType.CHANNEL:
                if mention.target_id is None or not channel_exists(
                    self._db, mention.target_id
                ):
                    m.valid = False
                    m.error = "Channel not found"

            elif mention.mention_type in (MentionType.EVERYONE, MentionType.HERE):
                # SECURITY: when the ``servers`` module is not
                # present, prior code short-circuited any absence of a
                # permission check to allow. We now consult the
                # configurable tag
                # ``notifications.allow_everyone_when_servers_missing``
                # (default FALSE) so a deployment that hasn't fully
                # wired the servers module cannot accidentally spam
                # everyone. Operators with a legitimate reason to
                # opt in (rare; e.g. a stripped test environment) do
                # so via config.
                if not self._servers:
                    allow_missing = False
                    try:
                        cfg = _mention_config.get("notifications", {}) or {}
                        allow_missing = bool(
                            cfg.get(
                                "allow_everyone_when_servers_missing",
                                False,
                            )
                        )
                    except Exception:
                        allow_missing = False
                    if not allow_missing:
                        m.valid = False
                        m.error = (
                            "Servers module not loaded and "
                            "notifications.allow_everyone_when_servers_missing=false; "
                            "@everyone/@here denied"
                        )
                elif server_id and channel_id is not None:
                    if not has_mention_everyone_permission(
                        self._servers, user_id, server_id, channel_id
                    ):
                        m.valid = False
                        m.error = "No permission to mention everyone"
                elif server_id:
                    if not self._servers.has_permission(
                        user_id, server_id, "messages.mention_everyone"
                    ):
                        m.valid = False
                        m.error = "No permission to mention everyone"
                else:
                    m.valid = False
                    m.error = "Cannot use @everyone/@here in DMs"

            validated.append(m)

        return validated

    def highlight_mentions(
        self, content: str, user_id: SnowflakeID
    ) -> List[MentionPosition]:
        mentions = self.parse_mentions(content)
        positions = []

        user_roles = set()
        rows = self._db.fetch_all(
            """SELECT mr.role_id FROM srv_member_roles mr
               JOIN srv_members m ON mr.member_id = m.id
               WHERE m.user_id = ?""",
            (user_id,),
        )
        for row in rows:
            user_roles.add(row["role_id"])

        for mention in mentions:
            is_self = False

            if mention.mention_type == MentionType.USER:
                is_self = mention.target_id == user_id
            elif mention.mention_type == MentionType.ROLE:
                is_self = mention.target_id in user_roles
            elif mention.mention_type in (MentionType.EVERYONE, MentionType.HERE):
                is_self = True

            positions.append(
                MentionPosition(
                    start_pos=mention.start_pos,
                    end_pos=mention.end_pos,
                    mention_type=mention.mention_type,
                    is_self=is_self,
                )
            )

        return positions
