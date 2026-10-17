"""
Member, ban, and invite handler for server operations.
"""

import string
import secrets
import threading
import time
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
    InviteRateLimitedError,
    InviteLimitReachedError,
    PermissionDeniedError,
    BanPurgeNotSupportedError,
)
from ..permission_utils import can_manage_member
from src.core.database.cache import cached, invalidate_pattern
from ..manager.converters import _row_to_member, _row_to_ban, _row_to_invite
import utils.logger as logger
from src.utils.encryption import encrypt_data

# CORRECTNESS FIX: get_member is wrapped in @cached; with allowlist-only cache
# reconstruction an unregistered dataclass comes back from a cache hit as a
# plain dict, crashing attribute-style consumers (e.g. role_handler's
# member.timeout_until). Register the type so hits reconstruct properly.
from src.core.database.cache.serialization import register_reconstructable_type

register_reconstructable_type(Member)


# Per-user invite-creation sliding window (in-process; the global rate-limit
# middleware covers transport-level floods, this guards the invite-minting
# endpoint itself). Keyed by user_id, bounded by config.
_invite_creation_lock = threading.Lock()
_invite_creation_windows: dict = {}


def _invite_config() -> dict:
    try:
        import utils.config as config

        return config.get("invites", {}) or {}
    except Exception:  # pragma: no cover - config loader always present
        return {}


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
        """Check if user is a member of server (cached in Valkey)."""
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

        # 2. Try Valkey
        from src.core.database import cache_get, cache_set, valkey_available

        if valkey_available():
            valkey_cached = cache_get(cache_key)
            if valkey_cached is not None:
                is_member = bool(int(valkey_cached))
                self.manager._cache_set(
                    self.manager._member_cache_prefix, cache_key, is_member
                )
                return is_member

        # 3. Check if user is the owner
        owner_id = self.manager._cache_get(self.manager._server_owner_cache_prefix, sid)
        if owner_id is None:
            # Check Valkey for owner
            owner_cache_key = f"server_owner:{sid}"
            if valkey_available():
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
                    if valkey_available():
                        cache_set(owner_cache_key, str(owner_id), ttl=3600)

        if owner_id == uid:
            self.manager._cache_set(self.manager._member_cache_prefix, cache_key, True)
            if valkey_available():
                cache_set(cache_key, "1", ttl=300)
            return True

        # 4. Final DB check
        row = self.db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?", (sid, uid)
        )
        is_member = row is not None

        # Cache result
        self.manager._cache_set(self.manager._member_cache_prefix, cache_key, is_member)
        if valkey_available():
            cache_set(cache_key, "1" if is_member else "0", ttl=300)

        return is_member

    def add_member(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        inviter_id: Optional[SnowflakeID] = None,
        temporary: bool = False,
    ) -> Member:
        """Add a user as a member of a server.

        ``temporary`` marks a membership granted by a temporary invite; it is
        revoked when the member's last connection closes unless they were
        granted a role in the meantime (Agent 16 F9).
        """
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
                """INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at, inviter_id, is_temporary)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    member_id,
                    server_id,
                    user_id,
                    now,
                    now,
                    inviter_id,
                    1 if temporary else 0,
                ),
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

        self.manager._invalidate_member_caches(server_id, user_id)
        self.manager.get_servers.invalidate(user_id)

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

        # Changes applied to ANOTHER member (moderation fields OR a nickname)
        # must respect the same hierarchy rules as kick/ban: the actor must be
        # able to manage the target and the owner can never be targeted. A
        # moderator holding only ``members.manage_nicknames`` must not be able
        # to rename members ranked equal to or above them, so the nickname is
        # part of this gate rather than exempt from it. The system actor
        # (user_id == 0, e.g. AutoMod) is exempt.
        # A nickname change applied to ANOTHER member requires the
        # manage_nicknames permission. Check it before the hierarchy gate so a
        # caller who simply lacks the permission gets a permission error, while
        # a moderator who holds it but is outranked gets the hierarchy error.
        if nickname is not None and user_id != member_user_id:
            self.manager.require_permission(
                user_id, server_id, "members.manage_nicknames"
            )

        moderating_other = (
            user_id != member_user_id
            and user_id != 0
            and any(
                v is not None
                for v in (
                    muted,
                    deafened,
                    timeout_until,
                    timeout_reason,
                    nickname,
                )
            )
        )
        if moderating_other:
            server = self.manager.get_server(server_id, user_id)
            if not server:
                raise PermissionDeniedError(
                    "Not a member of this server", "members.manage_roles"
                )
            if server.owner_id == member_user_id:
                raise CannotModifyOwnerError("Cannot moderate the server owner")
            user_roles = self.manager._get_member_role_rows(server_id, user_id)
            target_roles = self.manager._get_member_role_rows(server_id, member_user_id)
            if not can_manage_member(
                user_roles, target_roles, server.owner_id == user_id, False
            ):
                raise RoleHierarchyError(
                    "Cannot moderate a member with equal or higher role"
                )

        updates = []
        params: List[Any] = []
        changes = {}

        if nickname is not None:
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

        if timeout_until is not None or timeout_reason is not None:
            # Timeouts are a moderation action. The system actor (user_id == 0,
            # e.g. AutoMod) is allowed to apply them without an explicit role,
            # but a real user must hold members.manage_roles.
            if user_id != 0:
                self.manager.require_permission(
                    user_id, server_id, "members.manage_roles"
                )

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
            allowed_columns = {
                "nickname",
                "muted",
                "deafened",
                "timeout_until",
                "timeout_reason",
            }

            # Single atomic UPDATE: one statement assigns every changed
            # column plus updated_at, so a mid-update failure cannot leave a
            # half-written row and the write costs one round trip instead of
            # one per column. Column names come only from the allow-list
            # (values stay parameterized) and the SET clause is joined, so
            # the static SQL-guard audit keeps passing.
            set_columns = []
            set_params = []
            for update, value in zip(updates, params):
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")
                set_columns.append(col_name)
                set_params.append(value)
            now = self.manager._get_timestamp()
            set_columns.append("updated_at")
            set_params.append(now)
            set_clause = ", ".join(f"{col} = ?" for col in set_columns)
            query = (
                "UPDATE srv_members SET "
                + set_clause
                + " WHERE server_id = ? AND user_id = ?"
            )
            self.db.execute(query, (*set_params, server_id, member_user_id))
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
        # Timeout (and other member state) feeds permission computation, so the
        # user's cached permission snapshots must be dropped across all tiers.
        self.manager._invalidate_permission_caches(server_id, member_user_id)
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
        if not server:
            # Actor is not a member of this server (or server missing) -
            # fail with a permission error instead of dereferencing None.
            raise PermissionDeniedError("Not a member of this server", "members.kick")
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

        self.manager._invalidate_member_caches(server_id, member_user_id)
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
            member = self.add_member(
                invite.server_id,
                user_id,
                invite.inviter_id,
                temporary=bool(getattr(invite, "temporary", False)),
            )
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

    def purge_temporary_members(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        """Revoke a user's temporary memberships that earned no role.

        A temporary invite grants membership for the session only: when the
        member's last connection closes, the membership is revoked unless
        they were granted a role (any role other than the server's default).
        This is what turns the stored ``temporary`` invite flag into actual
        behavior instead of a silently ignored value (Agent 16 F9).

        Returns the server IDs the membership was revoked from.
        """
        rows = self.db.fetch_all(
            "SELECT id AS member_id, server_id FROM srv_members "
            "WHERE user_id = ? AND is_temporary = 1",
            (user_id,),
        )
        revoked: List[SnowflakeID] = []
        for row in rows or []:
            member_id = row["member_id"]
            server_id = row["server_id"]

            # A role other than the server's default makes the membership
            # permanent (the member contributed/participated).
            earned = self.db.fetch_one(
                "SELECT 1 FROM srv_member_roles mr "
                "JOIN srv_roles r ON r.id = mr.role_id "
                "WHERE mr.member_id = ? AND r.is_default = 0",
                (member_id,),
            )
            if earned:
                self.db.execute(
                    "UPDATE srv_members SET is_temporary = 0 WHERE id = ?",
                    (member_id,),
                )
                continue

            self.db.execute(
                "DELETE FROM srv_member_roles WHERE member_id = ?", (member_id,)
            )
            self.db.execute("DELETE FROM srv_members WHERE id = ?", (member_id,))
            self.manager._invalidate_member_caches(server_id, user_id)
            self.manager._log_audit(
                server_id,
                user_id,
                AuditLogAction.MEMBER_LEAVE,
                "member",
                user_id,
            )
            revoked.append(server_id)

        if revoked:
            logger.info(
                f"Revoked {len(revoked)} temporary membership(s) for user {user_id}"
            )
        return revoked

    def delete_invite(self, user_id: SnowflakeID, code: str) -> bool:
        """Revoke an invite (soft-revoke).

        The row is kept with ``revoked = 1`` — every read path
        (``get_invite``, ``get_invites``, ``use_invite``) filters
        ``revoked = 0`` — so historical audit references survive.
        """
        row = self.db.fetch_one(
            "SELECT * FROM srv_invites WHERE code = ? AND revoked = 0", (code,)
        )
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

        self.db.execute(
            "UPDATE srv_invites SET revoked = 1 WHERE code = ? AND revoked = 0",
            (code,),
        )

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

    def _enforce_invite_quota(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> None:
        """Rate-limit and cap invite creation per user / per server.

        Configured under the ``invites`` config section:
        - ``creation_max_per_window`` (default 20) invites per
          ``creation_window_seconds`` (default 3600) per user;
        - ``max_active_per_user`` (default 25) and
          ``max_active_per_server`` (default 100) concurrently-live
          (unrevoked, unexpired) invites.
        """
        cfg = _invite_config()

        # --- Per-user creation rate limit (sliding window) ---
        window_seconds = int(cfg.get("creation_window_seconds", 3600))
        window_max = int(cfg.get("creation_max_per_window", 20))
        if window_max > 0:
            now = time.monotonic()
            key = str(user_id)
            with _invite_creation_lock:
                timestamps = [
                    ts
                    for ts in _invite_creation_windows.get(key, [])
                    if now - ts < window_seconds
                ]
                if len(timestamps) >= window_max:
                    raise InviteRateLimitedError(
                        "Invite creation rate limit exceeded",
                        retry_after_seconds=window_seconds,
                    )
                timestamps.append(now)
                _invite_creation_windows[key] = timestamps

        # --- Active-invite quotas ---
        now_ms = self.manager._get_timestamp()
        live_predicate = "(expires_at IS NULL OR expires_at > ?)"

        max_per_user = int(cfg.get("max_active_per_user", 25))
        if max_per_user > 0:
            row = self.db.fetch_one(
                f"SELECT COUNT(*) AS c FROM srv_invites "
                f"WHERE inviter_id = ? AND revoked = 0 AND {live_predicate}",  # nosec: B608 - constant predicate
                (user_id, now_ms),
            )
            if row and row["c"] >= max_per_user:
                raise InviteLimitReachedError(
                    "Active invite limit reached for this user",
                    limit=max_per_user,
                )

        max_per_server = int(cfg.get("max_active_per_server", 100))
        if max_per_server > 0:
            row = self.db.fetch_one(
                f"SELECT COUNT(*) AS c FROM srv_invites "
                f"WHERE server_id = ? AND revoked = 0 AND {live_predicate}",  # nosec: B608 - constant predicate
                (server_id, now_ms),
            )
            if row and row["c"] >= max_per_server:
                raise InviteLimitReachedError(
                    "Active invite limit reached for this server",
                    limit=max_per_server,
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

        self._enforce_invite_quota(user_id, channel.server_id)

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
