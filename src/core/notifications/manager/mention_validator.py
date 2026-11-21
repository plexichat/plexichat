from typing import Dict, Optional, List

import utils.config as _mention_config

from ..models import Mention, MentionType, MentionPosition
from ..parser import parse_mentions as _parse_mentions
from src.core.base import SnowflakeID
from .helpers import (
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

        # Per-call memo of role rows. ``role_exists`` + ``get_role`` used to
        # issue two identical ``SELECT ... FROM srv_roles WHERE id = ?`` round
        # trips for every role mention (and re-issue both for a role mentioned
        # twice in one message). Fetch the row once per distinct role id and
        # derive existence from it. The memo is local to this one validation
        # call, holds only public server role metadata, and never crosses a
        # request or worker boundary, so it adds no cross-user leak and no
        # multi-node staleness window. Fail-closed semantics are unchanged: a
        # missing row still marks the mention invalid, and the
        # cross-server / non-mentionable checks still run against the same row.
        roles_by_id: Dict[SnowflakeID, Optional[dict]] = {}

        def _role_row(role_id: SnowflakeID) -> Optional[dict]:
            if role_id not in roles_by_id:
                roles_by_id[role_id] = get_role(self._db, role_id)
            return roles_by_id[role_id]

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
                # SECURITY: roles are server-scoped. In a DM/server-less
                # context there is no server against which to resolve the
                # role, so a ``<@&role_id>`` cannot be authorized. Prior code
                # skipped every check when ``server_id`` was unset, marking
                # the mention valid; the fan-out then notified *every* member
                # of that role in whatever server it belonged to, and the
                # channel-view recipient filter is a no-op for DMs (no
                # channel id). That leaked a DM's content preview to complete
                # strangers. Fail closed: reject role mentions in DMs.
                if server_id is None:
                    m.valid = False
                    m.error = "Cannot mention roles without a server context"
                else:
                    role = (
                        _role_row(mention.target_id)
                        if mention.target_id is not None
                        else None
                    )
                    if role is None:
                        m.valid = False
                        m.error = "Role not found"
                    elif role["server_id"] != server_id:
                        m.valid = False
                        m.error = "Role not in this server"
                    elif not bool(role["mentionable"]):
                        # roles.manage is a SERVER-scoped permission. Resolving
                        # it with a channel_id applies per-channel overrides,
                        # which would let a member granted roles.manage in ONE
                        # channel bypass a role's server-wide non-mentionable
                        # setting and mass-ping that role. Check it at server
                        # scope so only a genuine server-wide grant (or
                        # owner/administrator) authorizes the bypass.
                        if not self._servers or not self._servers.has_permission(
                            user_id, server_id, "roles.manage"
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
                    # If we have a server_id, the message is in a server context
                    # but the servers module isn't loaded. Check config for fallback.
                    if server_id:
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
                    else:
                        # No server_id means DM context - @everyone/@here not allowed in DMs
                        m.valid = False
                        m.error = "Cannot use @everyone/@here in DMs"
                elif server_id and channel_id:
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
