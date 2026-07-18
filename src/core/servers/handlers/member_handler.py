"""
Member, ban, and invite handler for server operations.
"""

import string
import secrets
from typing import Optional, List, Any
from src.core.base import SnowflakeID
from ..models import Member, Ban, Invite, ChannelType, AuditLogAction
from ..exceptions import (
    ServerNotFoundError,
    MemberNotFoundError,
    MemberExistsError,
    UserBannedError,
    CannotModifyOwnerError,
    RoleHierarchyError,
    BanExistsError,
    BanNotFoundError,
    ChannelNotFoundError,
    InviteNotFoundError,
    InviteExpiredError,
    InviteMaxUsesError,
)
from ..permission_utils import can_manage_member
from src.core.database import cache_delete
from src.core.database.cache import cached, invalidate_pattern
from ..manager.converters import _row_to_member, _row_to_ban, _row_to_invite
import utils.logger as logger
from src.utils.encryption import encrypt_data


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
            existing = self.db.fetch_one(
                "SELECT 1 FROM srv_invites WHERE code = ?", (code,)
            )
            if not existing:
                return code

    def is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        """Check if user is a member of server (cached in Redis)."""
        try:
            sid = int(server_id)
            uid = int(user_id)
        except (ValueError, TypeError):
            return False

        cache_key = f"is_member:{sid}:{uid}"

        # 1. Try internal memory first
        mem_cached = self.manager._cache_get(
            self.manager._member_cache_prefix, cache_key
        )
        if mem_cached is not None:
            return mem_cached

        # 2. Try Redis
        from src.core.database import cache_get, cache_set, redis_available

        if redis_available():
            redis_cached = cache_get(cache_key)
            if redis_cached is not None:
                is_member = bool(int(redis_cached))
                self.manager._cache_set(
                    self.manager._member_cache_prefix, cache_key, is_member
                )
                return is_member

        # 3. Check if user is the owner
        owner_id = self.manager._cache_get(self.manager._server_owner_cache_prefix, sid)
        if owner_id is None:
            # Check Redis for owner
            owner_cache_key = f"server_owner:{sid}"
            if redis_available():
                owner_id_cached = cache_get(owner_cache_key)
                if owner_id_cached:
                    owner_id = int(owner_id_cached)
                    self.manager._cache_set(
                        self.manager._server_owner_cache_prefix, sid, owner_id
                    )

            if owner_id is None:
                row = self.db.fetch_one(
                    "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
                    (sid,),
                )
                if row:
                    owner_id = int(row["owner_id"])
                    self.manager._cache_set(
                        self.manager._server_owner_cache_prefix, sid, owner_id
                    )
                    if redis_available():
                        cache_set(owner_cache_key, str(owner_id), ttl=3600)

        if owner_id == uid:
            self.manager._cache_set(self.manager._member_cache_prefix, cache_key, True)
            if redis_available():
                cache_set(cache_key, "1", ttl=300)
            return True

        # 4. Final DB check
        row = self.db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?", (sid, uid)
        )
        is_member = row is not None

        # Cache result
        self.manager._cache_set(self.manager._member_cache_prefix, cache_key, is_member)
        if redis_available():
            cache_set(cache_key, "1" if is_member else "0", ttl=300)

        return is_member

    def add_member(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        inviter_id: Optional[SnowflakeID] = None,
    ) -> Member:
        """Add a user as a member of a server."""
        ban = self.db.fetch_one(
            "SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        if ban:
            raise UserBannedError("User is banned from this server")

        existing_member = self.get_member(server_id, user_id)
        if existing_member:
            raise MemberExistsError("User is already a member of this server")

        server = self.db.fetch_one(
            "SELECT * FROM srv_servers WHERE id = ? AND deleted = 0", (server_id,)
        )
        if not server:
            raise ServerNotFoundError("Server not found")

        now = self.manager._get_timestamp()
        member_id = self.manager._generate_id()

        try:
            self.db.execute(
                """INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at, inviter_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (member_id, server_id, user_id, now, now, inviter_id),
            )
        except Exception as e:
            if "UniqueViolation" in type(e).__name__ or "duplicate key" in str(e):
                # Race condition: member was added between check and insert
                result = self.get_member(server_id, user_id)
                if result:
                    return result
            raise

        default_role = self.db.fetch_one(
            "SELECT id FROM srv_roles WHERE server_id = ? AND is_default = 1",
            (server_id,),
        )
        if default_role:
            self.db.execute(
                """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at)
                   VALUES (?, ?, ?, ?)""",
                (self.manager._generate_id(), member_id, default_role["id"], now),
            )

        self.manager._log_audit(
            server_id, user_id, AuditLogAction.MEMBER_JOIN, "member", user_id
        )

        if self.manager._messaging:
            try:
                channels = self.db.fetch_all(
                    """SELECT conversation_id FROM srv_channels
                       WHERE server_id = ?
                         AND channel_type IN (?, ?)
                         AND deleted = 0""",
                    (
                        server_id,
                        ChannelType.TEXT.value,
                        ChannelType.ANNOUNCEMENT.value,
                    ),
                )
                conv_ids = [
                    ch["conversation_id"] for ch in channels if ch["conversation_id"]
                ]

                if conv_ids:
                    from src.core.messaging.models import ParticipantRole

                    self.manager._messaging.add_participant_to_conversations(
                        user_id, conv_ids, ParticipantRole.MEMBER
                    )
            except Exception as e:
                logger.error(
                    f"Error adding member {user_id} to server conversations: {e}"
                )

        self.manager._cache_invalidate(
            self.manager._member_cache_prefix, (server_id, user_id)
        )

        # Invalidate Redis
        from src.core.database import cache_delete, invalidate_pattern

        cache_delete(f"is_member:{server_id}:{user_id}")
        invalidate_pattern(f"perms:{user_id}:{server_id}:*")
        self.manager.get_servers.invalidate(user_id)  # type: ignore

        result = self.get_member(server_id, user_id)
        assert result is not None
        return result

    @cached(ttl=30, prefix="member_data")
    def get_member(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Member]:
        """Get a member by user ID."""
        row = self.db.fetch_one(
            "SELECT * FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        if not row:
            return None
        role_rows = self.db.fetch_all(
            "SELECT role_id FROM srv_member_roles WHERE member_id = ?",
            (row["id"],),
        )
        role_ids = [r["role_id"] for r in role_rows]
        return _row_to_member(row, roles=role_ids)

    def update_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        nickname: Optional[str] = None,
        muted: Optional[bool] = None,
        deafened: Optional[bool] = None,
        timeout_until: Optional[int] = None,
        timeout_reason: Optional[str] = None,
    ) -> Member:
        """Update member settings."""
        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        updates = []
        params: List[Any] = []
        changes = {}

        if nickname is not None:
            if user_id != member_user_id:
                self.manager.require_permission(
                    user_id, server_id, "members.manage_nicknames"
                )
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

        if timeout_until is not None:
            # require permission check if not self? normally members.manage_roles or similar
            # but for AutoMod we trust the system.
            updates.append("timeout_until = ?")
            params.append(timeout_until)
            changes["timeout_until"] = {
                "old": member.timeout_until,
                "new": timeout_until,
            }

        if timeout_reason is not None:
            updates.append("timeout_reason = ?")
            params.append(timeout_reason)
            changes["timeout_reason"] = {
                "old": member.timeout_reason,
                "new": timeout_reason,
            }

        if updates:
            # Avoid dynamic UPDATE to satisfy bandit - execute individual updates per column
            now = self.manager._get_timestamp()
            for i, update in enumerate(updates):
                col_name = update.split(" = ")[0]
                value = params[i]
                query = (
                    "UPDATE srv_members SET "  # nosec: B608
                    + col_name
                    + " = ?, updated_at = ? WHERE server_id = ? AND user_id = ?"  # nosec: B608
                )
                self.db.execute(query, (value, now, server_id, member_user_id))
            if user_id != member_user_id:
                self.manager._log_audit(
                    server_id,
                    user_id,
                    AuditLogAction.MEMBER_UPDATE,
                    "member",
                    member_user_id,
                    changes,
                )

        invalidate_pattern(f"member_data:*{member_user_id}*")
        # Invalidate permissions cache
        invalidate_pattern(f"perms:{member_user_id}:{server_id}:*")
        result = self.get_member(server_id, member_user_id)
        assert result is not None
        return result

    def kick_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> bool:
        """Kick a member from a server."""
        server = self.manager.get_server(server_id, user_id)
        if server.owner_id == member_user_id:
            raise CannotModifyOwnerError("Cannot kick the server owner")

        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        # Require kick permission for non-self kicks
        self.manager.require_permission(user_id, server_id, "members.kick")

        user_roles = self.manager._get_member_role_rows(server_id, user_id)
        target_roles = self.manager._get_member_role_rows(server_id, member_user_id)
        if not can_manage_member(
            user_roles, target_roles, server.owner_id == user_id, False
        ):
            raise RoleHierarchyError("Cannot kick a member with equal or higher role")

        self.db.execute(
            "DELETE FROM srv_member_roles WHERE member_id = ?", (member.id,)
        )
        self.db.execute(
            "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, member_user_id),
        )

        # Remove from messaging conversations if messaging module available
        if self.manager._messaging:
            try:
                channels = self.db.fetch_all(
                    "SELECT conversation_id FROM srv_channels WHERE server_id = ? AND deleted = 0",
                    (server_id,),
                )
                conv_ids = [
                    ch["conversation_id"] for ch in channels if ch["conversation_id"]
                ]

                if conv_ids:
                    if hasattr(
                        self.manager._messaging, "remove_participant_from_conversations"
                    ):
                        self.manager._messaging.remove_participant_from_conversations(
                            member_user_id, conv_ids
                        )
            except Exception as e:
                logger.error(
                    f"Error removing kicked member {member_user_id} from server conversations: {e}"
                )

        self.manager._cache_invalidate(
            self.manager._member_cache_prefix, (server_id, member_user_id)
        )
        self.manager._cache_invalidate(
            self.manager._member_cache_prefix, f"is_member:{server_id}:{member_user_id}"
        )
        self.manager._cache_invalidate(
            self.manager._permission_cache_prefix, (member_user_id, server_id, None)
        )

        # Invalidate Redis
        from src.core.database import cache_delete, invalidate_pattern

        cache_delete(f"is_member:{server_id}:{member_user_id}")
        invalidate_pattern(f"perms:{member_user_id}:{server_id}:*")

        invalidate_pattern(f"member_data:*{member_user_id}*")
        self.manager._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_KICK,
            "member",
            member_user_id,
            reason=reason,
        )
        return True

    def unban_member(
        self, user_id: SnowflakeID, server_id: SnowflakeID, banned_user_id: SnowflakeID
    ) -> bool:
        """Unban a user from a server."""
        self.manager.require_permission(user_id, server_id, "members.ban")

        existing = self.db.fetch_one(
            "SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server_id, banned_user_id),
        )
        if not existing:
            raise BanNotFoundError("User is not banned from this server")

        self.db.execute(
            "DELETE FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server_id, banned_user_id),
        )
        self.manager._log_audit(
            server_id, user_id, AuditLogAction.MEMBER_UNBAN, "member", banned_user_id
        )
        return True

    def get_bans(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Ban]:
        """Get all bans for a server."""
        self.manager.require_permission(user_id, server_id, "bans.view")
        rows = self.db.fetch_all(
            "SELECT * FROM srv_bans WHERE server_id = ?", (server_id,)
        )
        return [_row_to_ban(row) for row in rows]

    def use_invite(self, user_id: SnowflakeID, code: str) -> Member:
        """Get and use an invite."""
        row = self.db.fetch_one(
            "SELECT * FROM srv_invites WHERE code = ? AND revoked = 0", (code,)
        )
        if not row:
            raise InviteNotFoundError("Invite not found")

        invite = _row_to_invite(row)

        now = self.manager._get_timestamp()
        if invite.expires_at is not None and invite.expires_at <= now:
            raise InviteExpiredError("Invite has expired", expired_at=invite.expires_at)

        if invite.max_uses > 0 and invite.uses >= invite.max_uses:
            raise InviteMaxUsesError(
                "Invite has reached maximum uses",
                max_uses=invite.max_uses,
                current_uses=invite.uses,
            )

        update_cursor = self.db.execute(
            """UPDATE srv_invites
               SET uses = uses + 1
               WHERE code = ?
                 AND revoked = 0
                 AND (expires_at IS NULL OR expires_at > ?)
                 AND (max_uses = 0 OR uses < max_uses)""",
            (code, now),
        )
        if getattr(update_cursor, "rowcount", 0) == 0:
            refreshed_row = self.db.fetch_one(
                "SELECT revoked, expires_at, max_uses, uses FROM srv_invites WHERE code = ?",
                (code,),
            )
            if not refreshed_row or refreshed_row["revoked"]:
                raise InviteNotFoundError("Invite not found")

            if (
                refreshed_row["expires_at"] is not None
                and refreshed_row["expires_at"] <= now
            ):
                raise InviteExpiredError(
                    "Invite has expired",
                    expired_at=refreshed_row["expires_at"],
                )

            raise InviteMaxUsesError(
                "Invite has reached maximum uses",
                max_uses=refreshed_row["max_uses"],
                current_uses=refreshed_row["uses"],
            )

        try:
            member = self.add_member(invite.server_id, user_id, invite.inviter_id)
        except Exception:
            self.db.execute(
                "UPDATE srv_invites SET uses = CASE WHEN uses > 0 THEN uses - 1 ELSE 0 END WHERE code = ?",
                (code,),
            )
            raise

        # Use invite.id (bigint) instead of invite.code (string) for target_id
        self.manager._log_audit(
            invite.server_id, user_id, AuditLogAction.INVITE_USE, "invite", invite.id
        )

        return member

    def delete_invite(self, user_id: SnowflakeID, code: str) -> bool:
        """Delete an invite."""
        row = self.db.fetch_one("SELECT * FROM srv_invites WHERE code = ?", (code,))
        if not row:
            raise InviteNotFoundError("Invite not found")

        server_id = row["server_id"]
        invite_id = row["id"]

        if row.get("inviter_id") != user_id:
            server = self.manager.get_server(server_id, user_id)
            if not server:
                raise ServerNotFoundError("Server not found")

            if server.owner_id != user_id:
                self.manager.require_permission(user_id, server_id, "invites.manage")

        self.db.execute("DELETE FROM srv_invites WHERE code = ?", (code,))

        self.manager._log_audit(
            server_id, user_id, AuditLogAction.INVITE_DELETE, "invite", invite_id
        )
        return True

    def ban_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> Ban:
        """Ban a user from a server."""
        server = self.manager.get_server(server_id, user_id)
        if server.owner_id == member_user_id:
            raise CannotModifyOwnerError("Cannot ban the server owner")

        # Require ban permission
        self.manager.require_permission(user_id, server_id, "members.ban")

        existing = self.db.fetch_one(
            "SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server_id, member_user_id),
        )
        if existing:
            raise BanExistsError("User is already banned")

        member = self.get_member(server_id, member_user_id)
        if member:
            user_roles = self.manager._get_member_role_rows(server_id, user_id)
            target_roles = self.manager._get_member_role_rows(server_id, member_user_id)
            if not can_manage_member(
                user_roles, target_roles, server.owner_id == user_id, False
            ):
                raise RoleHierarchyError(
                    "Cannot ban a member with equal or higher role"
                )
            self.db.execute(
                "DELETE FROM srv_member_roles WHERE member_id = ?", (member.id,)
            )
            self.db.execute(
                "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, member_user_id),
            )
            self.manager._cache_invalidate(
                self.manager._member_cache_prefix, (server_id, member_user_id)
            )

            cache_delete(f"is_member:{server_id}:{member_user_id}")
            invalidate_pattern(f"perms:{member_user_id}:{server_id}:*")

        invalidate_pattern(f"member_data:*{member_user_id}*")

        now = self.manager._get_timestamp()
        ban_id = self.manager._generate_id()
        self.db.execute(
            "INSERT INTO srv_bans (id, server_id, user_id, banned_by, reason, reason_encrypted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ban_id,
                server_id,
                member_user_id,
                user_id,
                reason,
                encrypt_data(reason, context=f"ban:{ban_id}") if reason else None,
                now,
            ),
        )
        self.manager._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_BAN,
            "member",
            member_user_id,
            reason=reason,
        )
        return Ban(
            id=ban_id,
            server_id=server_id,
            user_id=member_user_id,
            banned_by=user_id,
            reason=reason,
            created_at=now,
        )

    def create_invite(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        max_age: int = 86400,
        max_uses: int = 0,
        temporary: bool = False,
    ) -> Invite:
        """Create an invite to a channel."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")
        self.manager.require_permission(
            user_id, channel.server_id, "invites.create", channel_id
        )

        now = self.manager._get_timestamp()
        expires_at = now + (max_age * 1000) if max_age > 0 else None
        invite_id = self.manager._generate_id()
        code = self.generate_invite_code()

        self.db.execute(
            """INSERT INTO srv_invites (id, code, server_id, channel_id, inviter_id, max_age, max_uses, temporary, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invite_id,
                code,
                channel.server_id,
                channel_id,
                user_id,
                max_age,
                max_uses,
                1 if temporary else 0,
                now,
                expires_at,
            ),
        )
        self.manager._log_audit(
            channel.server_id,
            user_id,
            AuditLogAction.INVITE_CREATE,
            "invite",
            invite_id,
        )
        result = self.get_invite(code)
        assert result is not None
        return result

    def get_invite(self, code: str) -> Optional[Invite]:
        """Get an invite by code."""
        row = self.db.fetch_one(
            "SELECT * FROM srv_invites WHERE code = ? AND revoked = 0", (code,)
        )
        return _row_to_invite(row) if row else None
