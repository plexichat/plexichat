"""Server bans: ban, unban and listing."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID
from ...models import AuditLogAction, Ban
from ...exceptions import (
    BanExistsError,
    BanNotFoundError,
    BanPurgeNotSupportedError,
    CannotModifyOwnerError,
    PermissionDeniedError,
    RoleHierarchyError,
)
from ...permission_utils import can_manage_member
from ...manager.converters import _row_to_ban
from src.utils.encryption import encrypt_data


class MemberBanMixin:
    """The ban list and the two operations that change it."""

    manager: Any
    db: Any

    def get_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> Any:
        """Provided by :class:`MemberCrudMixin` on the composed handler."""
        raise NotImplementedError

    def ban_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
        delete_message_days: int = 0,
    ) -> Ban:
        """Ban a user from a server."""
        server = self.manager.get_server(server_id, user_id)
        if not server:
            # Actor is not a member of this server (or server missing) -
            # fail with a permission error instead of dereferencing None.
            raise PermissionDeniedError("Not a member of this server", "members.ban")
        if delete_message_days:
            raise BanPurgeNotSupportedError(
                "delete_message_days (message purge on ban) is not supported; pass 0"
            )
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
        self.manager._invalidate_member_caches(server_id, member_user_id)

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

    def unban_member(
        self, user_id: SnowflakeID, server_id: SnowflakeID, banned_user_id: SnowflakeID
    ) -> bool:
        """Unban a user from a server."""
        self.manager.require_permission(user_id, server_id, "members.ban")

        server = self.manager.get_server(server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id
        target_is_owner = server is not None and server.owner_id == banned_user_id

        # The owner can never be banned; guard against it regardless.
        if target_is_owner:
            raise CannotModifyOwnerError("Cannot unban the server owner")

        # Apply the same role-hierarchy rules as ban/kick. The banned user may
        # no longer be a member (their rows are removed on ban), in which case
        # target_roles is empty and any permitted moderator may unban them.
        user_roles = self.manager._get_member_role_rows(server_id, user_id)
        target_roles = self.manager._get_member_role_rows(server_id, banned_user_id)
        if not can_manage_member(user_roles, target_roles, is_owner, target_is_owner):
            raise RoleHierarchyError("Cannot unban a member with equal or higher role")

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
        # Viewing the ban list is a ban-management action. Allow anyone who can
        # ban members, or any actor explicitly granted the dedicated bans.view
        # permission. (bans.view is part of SERVER_PERMISSIONS so owners and
        # administrators implicitly satisfy it via the all-true permission set.)
        if not (
            self.manager.has_permission(user_id, server_id, "members.ban")
            or self.manager.has_permission(user_id, server_id, "bans.view")
        ):
            raise PermissionDeniedError(
                "Missing required permission: members.ban or bans.view",
                "members.ban",
            )
        rows = self.db.fetch_all(
            "SELECT * FROM srv_bans WHERE server_id = ?", (server_id,)
        )
        return [_row_to_ban(row) for row in rows]
