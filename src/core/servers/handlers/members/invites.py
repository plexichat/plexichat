"""Server invites: minting, quotas, redemption and revocation."""

import secrets
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
# endpoint itself). Keyed by user_id. The configured ``window_max`` bounds the
# timestamps *within* one bucket but not the number of buckets: without a
# cap/TTL a key is left behind for every user who ever minted an invite. Idle
# buckets are pruned and the map has a hard entry ceiling.
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

# One statement re-checks BOTH live-invite caps at the instant of the insert.
# A separate SELECT-then-INSERT (as this code used to do) is a TOCTOU: two
# concurrent creators can both read a count below the cap and both insert,
# pushing the server/user over the limit. The guard subqueries see the table
# state *before* the new row exists, so exactly the callers that still fit get
# a row. The ``? <= 0`` branches keep a disabled (non-positive) cap a no-op.
#
# SQLite serialises this single statement with its write lock, so the guard is
# authoritative there. PostgreSQL's READ COMMITTED snapshot would let two
# concurrent statements both pass, so create_invite() additionally takes
# advisory xact locks around this insert (see there) to serialise the counts.
_ATOMIC_INVITE_INSERT_SQL = """
INSERT INTO srv_invites
    (id, code, server_id, channel_id, inviter_id, max_age, max_uses,
     temporary, created_at, expires_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE ( ? <= 0 OR
        (SELECT COUNT(*) FROM srv_invites
         WHERE inviter_id = ? AND revoked = 0
           AND (expires_at IS NULL OR expires_at > ?)) < ? )
  AND ( ? <= 0 OR
        (SELECT COUNT(*) FROM srv_invites
         WHERE server_id = ? AND revoked = 0
           AND (expires_at IS NULL OR expires_at > ?)) < ? )
"""

_invite_creation_lock = threading.Lock()
_invite_creation_windows: dict = {}

# Idle buckets older than this are reclaimed regardless of the configured
# window. Pruning uses ``max(configured_window, this_value)`` so a long
# configured window can never be shortened by reclamation (which would weaken
# the rate limit); the hard cap bounds the map even if that window is huge.
_INVITE_WINDOW_TTL_SECONDS = 86_400
_INVITE_WINDOW_MAX_KEYS = 8192


def _reset_invite_windows() -> None:
    """Drop every invite-creation window (tests / explicit reclamation only)."""
    with _invite_creation_lock:
        _invite_creation_windows.clear()


def _check_invite_creation_rate(user_id, cfg=None) -> None:
    """Apply the per-user sliding-window invite-creation limit.

    Behavior is identical to the previous inline block; the only addition is
    reclaiming idle buckets and enforcing a hard entry cap so the registry
    cannot grow with the number of users that ever created an invite.
    """
    if cfg is None:
        cfg = _invite_config()
    window_seconds = int(cfg.get("creation_window_seconds", 3600))
    window_max = int(cfg.get("creation_max_per_window", 20))
    if window_max <= 0:
        return
    now = time.monotonic()
    key = str(user_id)
    with _invite_creation_lock:
        # Reclaim buckets whose newest timestamp is older than the retention
        # window. Use the larger of the configured window and the TTL floor so
        # pruning can never drop timestamps the configured window still counts.
        retention = max(window_seconds, _INVITE_WINDOW_TTL_SECONDS)
        stale = [
            k
            for k, stamps in _invite_creation_windows.items()
            if not stamps or now - stamps[-1] >= retention
        ]
        for k in stale:
            _invite_creation_windows.pop(k, None)
        # Hard ceiling. Dicts keep insertion order, so the oldest bucket goes
        # first; this only bites under a flood of distinct user ids.
        while len(_invite_creation_windows) >= _INVITE_WINDOW_MAX_KEYS:
            _invite_creation_windows.pop(next(iter(_invite_creation_windows)))

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
        ...

    # An invite code is a bearer credential: whoever holds it can join the
    # server. The old scheme (``secrets.choice`` over 62 alphanumeric chars,
    # 8-12 chars long) carried only 47.6-71.5 bits and was online-brute-
    # forceable. Draw at least 16 bytes (128 bits) from the CSPRNG; urlsafe
    # base64 encodes that as 22 characters in the ``[A-Za-z0-9_-]`` alphabet
    # accepted by the join/invite validators. A larger configured byte count
    # is honoured, but legacy short ``invite_code_length`` values can no longer
    # weaken the floor.
    _MIN_INVITE_CODE_BYTES = 16

    def generate_invite_code(self) -> str:
        """Generate a unique, cryptographically strong invite code.

        Uses ``secrets.token_urlsafe`` so every code has at least 128 bits of
        entropy (22 urlsafe characters), never a short/sequential/id-derived
        value. The DB collision loop preserves the UNIQUE(code) contract.
        """
        configured = self.manager._config.get("invite_code_length", 8)
        try:
            num_bytes = max(self._MIN_INVITE_CODE_BYTES, int(configured))
        except (TypeError, ValueError):
            num_bytes = self._MIN_INVITE_CODE_BYTES
        while True:
            code = secrets.token_urlsafe(num_bytes)
            existing = self.db.fetch_one(
                "SELECT 1 FROM srv_invites WHERE code = ?", (code,)
            )
            if not existing:
                return code

    def _enforce_invite_quota(
        self, user_id: SnowflakeID, server_id: SnowflakeID, cfg: Optional[dict] = None
    ) -> None:
        """Rate-limit and *advisory*-cap invite creation per user / per server.

        Configured under the ``invites`` config section:
        - ``creation_max_per_window`` (default 20) invites per
          ``creation_window_seconds`` (default 3600) per user;
        - ``max_active_per_user`` (default 25) and
          ``max_active_per_server`` (default 100) concurrently-live
          (unrevoked, unexpired) invites.

        The per-user sliding window and these SELECT-based cap checks are a
        fast-fail / good-error-message layer only: a second creator can push a
        count past the cap between this SELECT and the later INSERT. The
        authoritative, race-free cap check lives in the atomic guarded INSERT
        in :meth:`create_invite`.

        ``cfg`` is an optional config snapshot supplied by the sole caller so
        the hot path reads ``invites`` config exactly once per mint; when it is
        omitted the helper still loads the config itself.
        """
        if cfg is None:
            cfg = _invite_config()

        # --- Per-user creation rate limit (sliding window) ---
        _check_invite_creation_rate(user_id, cfg)

        # The sliding-window registry above is process-local: under
        # ``--scale backend=N`` every worker keeps its own copy, so on its own
        # it multiplies a user's creation budget by N. Spend a slot in the
        # SHARED, atomic rate-limit backend as well so the configured budget is
        # enforced globally. ``check_rate_limit`` increments one shared counter
        # atomically and fails closed on a backend fault, so neither a
        # read-modify-write race nor a store outage can reopen the corridor.
        creation_max = int(cfg.get("creation_max_per_window", 20))
        if creation_max > 0:
            from src.core.database import check_rate_limit

            creation_window = int(cfg.get("creation_window_seconds", 3600))
            try:
                allowed, _remaining = check_rate_limit(
                    f"invites:creation:{user_id}", creation_max, creation_window
                )
            except Exception:
                # An out-of-range config or a limiter backend that cannot run
                # must deny, never silently become an unlimited path.
                allowed = False
            if not allowed:
                raise InviteRateLimitedError(
                    "Invite creation rate limit exceeded",
                    retry_after_seconds=creation_window,
                )

        # --- Active-invite quotas (advisory; see docstring) ---
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

    def _raise_active_invite_limit(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        now_ms: int,
        max_per_user: int,
        max_per_server: int,
    ) -> None:
        """Re-read the two caps after a losing guarded INSERT and raise the
        precise ``InviteLimitReachedError`` (always raises)."""
        if max_per_user > 0:
            row = self.db.fetch_one(_COUNT_LIVE_INVITES_BY_INVITER, (user_id, now_ms))
            if row and row["c"] >= max_per_user:
                raise InviteLimitReachedError(
                    "Active invite limit reached for this user",
                    limit=max_per_user,
                )
        if max_per_server > 0:
            row = self.db.fetch_one(_COUNT_LIVE_INVITES_BY_SERVER, (server_id, now_ms))
            if row and row["c"] >= max_per_server:
                raise InviteLimitReachedError(
                    "Active invite limit reached for this server",
                    limit=max_per_server,
                )
        # Defensive: a zero-row insert with no visible cap breach (e.g. a
        # concurrent revoke) must never fall through and return a phantom invite.
        raise InviteLimitReachedError("Active invite limit reached")

    def create_invite(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        max_age: int = 86400,
        max_uses: int = 0,
        temporary: bool = False,
    ) -> Invite:
        """Create an invite to a channel.

        The active-invite caps are enforced by a single conditional INSERT
        (:data:`_ATOMIC_INVITE_INSERT_SQL`) rather than a check-then-write, so
        concurrent creators can never both slip past a cap that only had room
        for one. On PostgreSQL the statement runs under advisory xact locks on
        both cap scopes to defeat the READ COMMITTED snapshot race; SQLite's
        single-writer transaction already serialises it.
        """
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")
        self.manager.require_permission(
            user_id, channel.server_id, "invites.create", channel_id
        )

        # Read the invites config once and reuse the same snapshot for both
        # the advisory quota checks and the authoritative INSERT guards, so a
        # single mint costs one config lookup instead of two (and the caps
        # cannot drift between the two reads if config changes mid-request).
        cfg = _invite_config()

        # Best-effort early feedback; the atomic INSERT below is authoritative.
        self._enforce_invite_quota(user_id, channel.server_id, cfg=cfg)

        max_per_user = int(cfg.get("max_active_per_user", 25))
        max_per_server = int(cfg.get("max_active_per_server", 100))

        now = self.manager._get_timestamp()
        expires_at = now + (max_age * 1000) if max_age > 0 else None
        invite_id = self.manager._generate_id()
        code = self.generate_invite_code()

        is_postgres = getattr(self.db, "type", "") == "postgres"
        if is_postgres:
            # PostgreSQL needs explicit serialisation: two workers evaluating
            # the guard subqueries under READ COMMITTED would otherwise both
            # see a stale count. Lock both scopes in a FIXED order (server,
            # then inviter) so callers for different scope pairs cannot deadlock.
            # Snowflakes are reduced to int4 for pg_advisory_xact_lock(int,int);
            # collisions only over-serialise, never weaken the guard.
            self.db.begin_transaction()
        try:
            if is_postgres:
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (2, int(channel.server_id) & 0x7FFFFFFF),
                )
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (1, int(user_id) & 0x7FFFFFFF),
                )
            cursor = self.db.execute(
                _ATOMIC_INVITE_INSERT_SQL,
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
                    max_per_user,
                    user_id,
                    now,
                    max_per_user,
                    max_per_server,
                    channel.server_id,
                    now,
                    max_per_server,
                ),
            )
            inserted = getattr(cursor, "rowcount", 0) == 1
            if is_postgres:
                self.db.commit()
        except Exception:
            if is_postgres:
                try:
                    self.db.rollback()
                except Exception:
                    pass
            raise

        if not inserted:
            # A concurrent creator filled a cap between the early check and
            # the insert. Report the exact limit that was hit.
            self._raise_active_invite_limit(
                user_id, channel.server_id, now, max_per_user, max_per_server
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

        # The early checks above are a fast-fail courtesy; the single guarded
        # UPDATE below is the authoritative consumption decision. Its expiry
        # predicate (``expires_at > ?``) must be evaluated against the clock at
        # the moment of the write, not the snapshot used for validation above:
        # an invite that expires between validation and the guarded write would
        # otherwise still be redeemed and grant membership after expiry (the
        # "passes validation, DB write lands after expiry" window). Uses and
        # revoked are already re-checked atomically by the statement itself;
        # re-reading the clock here closes the same gap for expires_at, and the
        # refreshed-row error path below reuses this same write-time value.
        now = self.manager._get_timestamp()
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
