"""
Member, ban, and invite handler for server operations.
"""

import string
import secrets
from typing import Optional, List
from src.core.base import SnowflakeID
from ..models import Member, Ban, Invite, ChannelType, AuditLogAction
from ..exceptions import (
    ServerNotFoundError,
    ServerAccessDeniedError,
    MemberNotFoundError,
    MemberExistsError,
    UserBannedError,
    CannotModifyOwnerError,
    RoleHierarchyError,
    BanExistsError,
    BanNotFoundError,
    InviteNotFoundError,
    InviteExpiredError,
    InviteMaxUsesError,
    ChannelNotFoundError,
)
from ..permissions import can_manage_member
import utils.logger as logger

class MemberHandler:
    def __init__(self, manager):
        self.manager = manager
        self.db = manager._db

    def generate_invite_code(self) -> str:
        """Generate a unique invite code."""
        length = self.manager._config.get("invite_code_length", 8)
        chars = string.ascii_letters + string.digits
        while True:
            code = "".join(secrets.choice(chars) for _ in range(length))
            existing = self.db.fetch_one("SELECT 1 FROM srv_invites WHERE code = ?", (code,))
            if not existing:
                return code

    def is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        """Check if user is a member of server (cached)."""
        try:
            sid = int(server_id)
            uid = int(user_id)
        except (ValueError, TypeError):
            return False

        cache_key = (sid, uid)
        cached = self.manager._cache_get(self.manager._member_cache, cache_key)
        if cached is True:
            return True

        owner_id = self.manager._cache_get(self.manager._server_owner_cache, sid)
        if owner_id is None:
            row = self.db.fetch_one("SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0", (sid,))
            if row:
                owner_id = int(row["owner_id"])
                self.manager._cache_set(self.manager._server_owner_cache, sid, owner_id)
        
        if owner_id == uid:
            self.manager._cache_set(self.manager._member_cache, cache_key, True)
            return True

        if cached is False:
            return False

        row = self.db.fetch_one("SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?", (sid, uid))
        is_member = row is not None
        self.manager._cache_set(self.manager._member_cache, cache_key, is_member)
        return is_member

    def add_member(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        inviter_id: Optional[SnowflakeID] = None,
    ) -> Member:
        """Add a user as a member of a server."""
        ban = self.db.fetch_one("SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?", (server_id, user_id))
        if ban:
            raise UserBannedError("User is banned from this server")

        if self.is_member(server_id, user_id):
            raise MemberExistsError("User is already a member of this server")

        server = self.db.fetch_one("SELECT * FROM srv_servers WHERE id = ? AND deleted = 0", (server_id,))
        if not server:
            raise ServerNotFoundError("Server not found")

        now = self.manager._get_timestamp()
        member_id = self.manager._generate_id()

        self.db.execute(
            """INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at, inviter_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (member_id, server_id, user_id, now, now, inviter_id),
        )

        default_role = self.db.fetch_one("SELECT id FROM srv_roles WHERE server_id = ? AND is_default = 1", (server_id,))
        if default_role:
            self.db.execute(
                """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at)
                   VALUES (?, ?, ?, ?)""",
                (self.manager._generate_id(), member_id, default_role["id"], now),
            )

        self.manager._log_audit(server_id, user_id, AuditLogAction.MEMBER_JOIN, "member", user_id)

        if self.manager._messaging:
            try:
                channels = self.db.fetch_all(
                    "SELECT conversation_id FROM srv_channels WHERE server_id = ? AND channel_type = ? AND deleted = 0",
                    (server_id, ChannelType.TEXT.value)
                )
                for ch in channels:
                    if ch["conversation_id"]:
                        from src.core.messaging.models import ParticipantRole
                        self.manager._messaging.add_participant(ch["conversation_id"], user_id, ParticipantRole.USER)
            except Exception as e:
                logger.error(f"Error adding member {user_id} to server conversations: {e}")

        self.manager._cache_invalidate(self.manager._member_cache, (server_id, user_id))
        result = self.get_member(server_id, user_id)
        assert result is not None
        return result

    def get_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Member]:
        """Get a member by user ID."""
        row = self.db.fetch_one("SELECT * FROM srv_members WHERE server_id = ? AND user_id = ?", (server_id, user_id))
        return self.manager._row_to_member(row) if row else None

    def update_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        nickname: Optional[str] = None,
        muted: Optional[bool] = None,
        deafened: Optional[bool] = None,
    ) -> Member:
        """Update member settings."""
        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        updates = []
        params = []
        changes = {}

        if nickname is not None:
            if user_id != member_user_id:
                self.manager.require_permission(user_id, server_id, "members.manage_nicknames")
            updates.append("nickname = ?")
            params.append(nickname if nickname else None)
            changes["nickname"] = {"old": member.nickname, "new": nickname}

        if muted is not None:
            self.manager.require_permission(user_id, server_id, "voice.mute_members")
            updates.append("muted = ?")
            params.append(1 if muted else 0)
            changes["muted"] = {"old": member.muted, "new": muted}

        if deafened is not None:
            self.manager.require_permission(user_id, server_id, "voice.deafen_members")
            updates.append("deafened = ?")
            params.append(1 if deafened else 0)
            changes["deafened"] = {"old": member.deafened, "new": deafened}

        if updates:
            params.extend([server_id, member_user_id])
            self.db.execute(f"UPDATE srv_members SET {', '.join(updates)} WHERE server_id = ? AND user_id = ?", tuple(params))
            if user_id != member_user_id:
                self.manager._log_audit(server_id, user_id, AuditLogAction.MEMBER_UPDATE, "member", member_user_id, changes)

        result = self.get_member(server_id, member_user_id)
        assert result is not None
        return result

    def kick_member(self, user_id: SnowflakeID, server_id: SnowflakeID, member_user_id: SnowflakeID, reason: Optional[str] = None) -> bool:
        """Kick a member from a server."""
        server = self.manager.get_server(server_id, user_id)
        if server.owner_id == member_user_id:
            raise CannotModifyOwnerError("Cannot kick the server owner")

        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        user_roles = self.manager._get_member_role_rows(server_id, user_id)
        target_roles = self.manager._get_member_role_rows(server_id, member_user_id)
        if not can_manage_member(user_roles, target_roles, server.owner_id == user_id, False):
            raise RoleHierarchyError("Cannot kick a member with equal or higher role")

        self.db.execute("DELETE FROM srv_member_roles WHERE member_id = ?", (member.id,))
        self.db.execute("DELETE FROM srv_members WHERE server_id = ? AND user_id = ?", (server_id, member_user_id))

        self.manager._cache_invalidate(self.manager._member_cache, (server_id, member_user_id))
        self.manager._cache_invalidate(self.manager._permission_cache, (member_user_id, server_id, None))
        self.manager._log_audit(server_id, user_id, AuditLogAction.MEMBER_KICK, "member", member_user_id, reason=reason)
        return True

    def ban_member(self, user_id: SnowflakeID, server_id: SnowflakeID, member_user_id: SnowflakeID, reason: Optional[str] = None) -> Ban:
        """Ban a user from a server."""
        server = self.manager.get_server(server_id, user_id)
        if server.owner_id == member_user_id:
            raise CannotModifyOwnerError("Cannot ban the server owner")

        existing = self.db.fetch_one("SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?", (server_id, member_user_id))
        if existing:
            raise BanExistsError("User is already banned")

        member = self.get_member(server_id, member_user_id)
        if member:
            user_roles = self.manager._get_member_role_rows(server_id, user_id)
            target_roles = self.manager._get_member_role_rows(server_id, member_user_id)
            if not can_manage_member(user_roles, target_roles, server.owner_id == user_id, False):
                raise RoleHierarchyError("Cannot ban a member with equal or higher role")
            self.db.execute("DELETE FROM srv_member_roles WHERE member_id = ?", (member.id,))
            self.db.execute("DELETE FROM srv_members WHERE server_id = ? AND user_id = ?", (server_id, member_user_id))
            self.manager._cache_invalidate(self.manager._member_cache, (server_id, member_user_id))

        now = self.manager._get_timestamp()
        ban_id = self.manager._generate_id()
        self.db.execute("INSERT INTO srv_bans (id, server_id, user_id, banned_by, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (ban_id, server_id, member_user_id, user_id, reason, now))
        self.manager._log_audit(server_id, user_id, AuditLogAction.MEMBER_BAN, "member", member_user_id, reason=reason)
        return Ban(id=ban_id, server_id=server_id, user_id=member_user_id, banned_by=user_id, reason=reason, created_at=now)

    def create_invite(self, user_id: SnowflakeID, channel_id: SnowflakeID, max_age: int = 86400, max_uses: int = 0, temporary: bool = False) -> Invite:
        """Create an invite to a channel."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        now = self.manager._get_timestamp()
        expires_at = now + (max_age * 1000) if max_age > 0 else None
        invite_id = self.manager._generate_id()
        code = self.generate_invite_code()

        self.db.execute(
            """INSERT INTO srv_invites (id, code, server_id, channel_id, inviter_id, max_age, max_uses, temporary, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (invite_id, code, channel.server_id, channel_id, user_id, max_age, max_uses, 1 if temporary else 0, now, expires_at),
        )
        self.manager._log_audit(channel.server_id, user_id, AuditLogAction.INVITE_CREATE, "invite", invite_id)
        return self.get_invite(code)

    def get_invite(self, code: str) -> Optional[Invite]:
        """Get an invite by code."""
        row = self.db.fetch_one("SELECT * FROM srv_invites WHERE code = ? AND revoked = 0", (code,))
        return self.manager._row_to_invite(row) if row else None
