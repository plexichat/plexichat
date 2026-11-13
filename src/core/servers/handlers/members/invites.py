"""Server invites: minting, quotas, redemption and revocation."""

import secrets
import string
import threading
import time
from typing import Any, Optional

from src.core.base import SnowflakeID
from ...models import AuditLogAction, Invite
from ...exceptions import (
    ChannelNotFoundError,
    InviteExpiredError,
    InviteLimitReachedError,
    InviteMaxUsesError,
    InviteNotFoundError,
    InviteRateLimitedError,
    ServerNotFoundError,
)
from ...manager.converters import _row_to_invite


# Per-user invite-creation sliding window (in-process; the global rate-limit
# middleware covers transport-level floods, this guards the invite-minting
# endpoint itself). Keyed by user_id, bounded by config.
_COUNT_LIVE_INVITES_BY_INVITER = (
    "SELECT COUNT(*) AS c FROM srv_invites "
    "WHERE inviter_id = ? AND revoked = 0 "
    "AND (expires_at IS NULL OR expires_at > ?)"
)
_COUNT_LIVE_INVITES_BY_SERVER = (
    "SELECT COUNT(*) AS c FROM srv_invites "
    "WHERE server_id = ? AND revoked = 0 "
    "AND (expires_at IS NULL OR expires_at > ?)"
)

_invite_creation_lock = threading.Lock()
_invite_creation_windows: dict = {}


def _invite_config() -> dict:
    try:
        import utils.config as config

        return config.get("invites", {}) or {}
    except Exception:  # pragma: no cover - config loader always present
        return {}


class MemberInviteMixin:
    """Invite codes and the quota that bounds their creation."""

    manager: Any
    db: Any

    def add_member(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        inviter_id: Optional[SnowflakeID] = None,
        temporary: bool = False,
    ) -> Any:
        """Provided by :class:`MemberCrudMixin` on the composed handler."""
        raise NotImplementedError

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

        max_per_user = int(cfg.get("max_active_per_user", 25))
        if max_per_user > 0:
            row = self.db.fetch_one(_COUNT_LIVE_INVITES_BY_INVITER, (user_id, now_ms))
            if row and row["c"] >= max_per_user:
                raise InviteLimitReachedError(
                    "Active invite limit reached for this user",
                    limit=max_per_user,
                )

        max_per_server = int(cfg.get("max_active_per_server", 100))
        if max_per_server > 0:
            row = self.db.fetch_one(_COUNT_LIVE_INVITES_BY_SERVER, (server_id, now_ms))
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

    def use_invite(self, user_id: SnowflakeID, code: str):
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
