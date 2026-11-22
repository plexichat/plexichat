from typing import Dict, Optional, List

import utils.config as _mention_config

from ..models import Mention, MentionType, MentionPosition
from ..parser import parse_mentions as _parse_mentions
from src.core.base import SnowflakeID
from .helpers import (
    get_role,
    channel_exists,
    has_mention_everyone_permission,
    role_covers_all_members,
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
        # Memo of the "role covers every server member" verdict. The
        # mass-mention gate and the per-mention decision below must agree, so
        # the (single) anti-join query is shared through this memo.
        all_member_roles: Dict[SnowflakeID, bool] = {}

        def _role_row(role_id: SnowflakeID) -> Optional[dict]:
            if role_id not in roles_by_id:
                roles_by_id[role_id] = get_role(self._db, role_id)
            return roles_by_id[role_id]

        def _is_all_member_role(role_id: SnowflakeID) -> bool:
            if role_id not in all_member_roles:
                all_member_roles[role_id] = role_covers_all_members(
                    self._db, role_id, server_id
                )
            return all_member_roles[role_id]

        def _is_everyone_role(mention) -> bool:
            """Whether a ROLE mention is an @everyone-equivalent mass ping.

            Every member holds the default (``is_default = 1``) role, so a
            ``<@&default_role_id>`` mention fans out to the entire server.
            A non-default role explicitly assigned to every member has the
            same fan-out (``create_notifications`` pings all of a role's
            members), so it must clear the same gate -- otherwise a "recreated
            everyone" role evades detection simply by lacking ``is_default``.
            Restrict the verdict to a role that exists and belongs to this
            server; the role row is memoised by ``_role_row`` (the validation
            loop below needs it too), so the flag check adds no extra query.
            """
            if server_id is None or mention.mention_type != MentionType.ROLE:
                return False
            if mention.target_id is None:
                return False
            role = _role_row(mention.target_id)
            if not role or role.get("server_id") != server_id:
                return False
            return bool(
                role.get("is_default") or _is_all_member_role(mention.target_id)
            )

        # SECURITY: an explicit ``@everyone``/``@here``, a mention of the
        # CURRENT channel (``<#this-channel>``), a mention of the server's
        # default ``@everyone`` role (``<@&default_role_id>``), AND a mention of
        # any role held by every server member all fan out to every member who
        # can view the channel (see
        # NotificationCreatorMixin.create_notifications). Each is therefore an
        # @everyone-equivalent mass ping and must clear the SAME fail-closed
        # permission gate. Compute that verdict once from the existing gate
        # (keeping a single audited call site) and apply it to all such mentions.
        # Without this, a member who lacks messages.mention_everyone can
        # mass-ping the whole channel/server by writing
        # ``<#the-channel-they-are-in>`` or ``<@&the-everyone-role>``.
        # Only messages that actually carry a mass mention pay the gate, so a
        # plain user/role/channel-link message keeps its existing query budget.
        needs_mass_mention_gate = any(
            m.mention_type in (MentionType.EVERYONE, MentionType.HERE)
            or (
                m.mention_type == MentionType.CHANNEL
                and channel_id is not None
                and m.target_id == channel_id
            )
            or _is_everyone_role(m)
            for m in mentions
        )
        mass_mention_error: Optional[str] = None
        if needs_mass_mention_gate:
            if not self._servers:
                # Server context but the servers module is not loaded: consult
                # the configurable opt-in (default FALSE). No server_id is a
                # DM, where mass mentions are always denied.
                if server_id:
                    allow_missing = False
                    try:
                        cfg = _mention_config.get("notifications", {}) or {}
                        allow_missing = bool(
                            cfg.get("allow_everyone_when_servers_missing", False)
                        )
                    except Exception:
                        allow_missing = False
                    if not allow_missing:
                        mass_mention_error = (
                            "Servers module not loaded and "
                            "notifications.allow_everyone_when_servers_missing=false; "
                            "@everyone/@here denied"
                        )
                else:
                    mass_mention_error = "Cannot use @everyone/@here in DMs"
            elif server_id and channel_id:
                if not has_mention_everyone_permission(
                    self._servers, user_id, server_id, channel_id
                ):
                    mass_mention_error = "No permission to mention everyone"
            elif server_id:
                if not self._servers.has_permission(
                    user_id, server_id, "messages.mention_everyone"
                ):
                    mass_mention_error = "No permission to mention everyone"
            else:
                mass_mention_error = "Cannot use @everyone/@here in DMs"

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
                    elif (
                        (
                            role.get("is_default")
                            or _is_all_member_role(mention.target_id)
                        )
                        and mass_mention_error is not None
                    ):
                        # Mentioning the default (@everyone) role -- or any
                        # role held by every member -- pings the whole server,
                        # an @everyone-equivalent mass ping. Gate it with the
                        # SAME fail-closed permission as @everyone/@here and a
                        # self-channel mention, even when the role is marked
                        # mentionable or the author holds roles.manage.
                        m.valid = False
                        m.error = mass_mention_error
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
                elif channel_id and mention.target_id == channel_id:
                    # Mentioning the channel the message was posted in pings
                    # every viewer -- an @everyone-equivalent mass ping. Gate
                    # it with the same permission as @everyone/@here.
                    if mass_mention_error is not None:
                        m.valid = False
                        m.error = mass_mention_error

            elif mention.mention_type in (MentionType.EVERYONE, MentionType.HERE):
                # SECURITY: the fail-closed mass-mention verdict is computed
                # once above (single audited permission-check call site).
                if mass_mention_error is not None:
                    m.valid = False
                    m.error = mass_mention_error

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
