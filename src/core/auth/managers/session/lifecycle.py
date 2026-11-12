"""Session lifecycle: create, extend, refresh, revoke and list sessions."""

import hashlib

from typing import List, Optional

import utils.logger as logger
from src.core.database import invalidate_pattern

from ...exceptions import TokenExpiredError, TokenInvalidError
from ...models import AuditEventType, Session
from ...tokens import create_session_token, parse_token, verify_token_hash
from ..protocol import AuthManagerProtocol


from typing import Dict, Tuple
from src.core.database import cached
from ...models import AccountType, TokenInfo
from ...permissions import permissions_from_json
from ...tokens import create_ws_ticket


class LifecycleMixin(AuthManagerProtocol):
    def _session_expire_hours(
        self,
        user_id: int,
        device_id: Optional[int],
        sessions_config: Dict,
    ) -> int:
        """Resolve the session TTL, honoring per-trust durations.

        Sessions on a trusted device (see auth_devices.trusted) use
        ``trusted_expire_hours`` when configured; untrusted or unknown-device
        sessions always use ``expire_hours``. A null/absent override disables
        the trusted duration entirely, making the feature opt-in per deploy.
        """
        if (
            device_id
            and sessions_config.get("trusted_expire_hours") is not None
            and self._device_is_trusted_by_id(user_id, device_id)
        ):
            return int(sessions_config["trusted_expire_hours"])
        return int(sessions_config.get("expire_hours", 720))

    def _device_is_trusted_by_id(self, user_id: int, device_id: int) -> bool:
        """Trust lookup keyed by the session's tracked device id (server-side
        row), never by a client-supplied fingerprint."""
        self._ensure_trusted_device_columns()
        row = self._db.fetch_one(
            "SELECT trusted FROM auth_devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        )
        return bool(row and row.get("trusted"))

    def _create_session(
        self,
        user_id: int,
        device_id: Optional[int],
        ip: Optional[str],
        ua: Optional[str],
    ) -> Session:
        sid = self._generate_id()
        now = self._get_timestamp()
        sessions_config = self._config.get("sessions", {})
        expire_hours = self._session_expire_hours(user_id, device_id, sessions_config)
        expires = now + (expire_hours * 3600 * 1000)
        token, token_hash = create_session_token(sid)

        ip_index = None
        ip_encrypted = None
        if ip:
            ip_index = self.crypto.blind_index(ip, "ip_address")
            ip_encrypted = self.crypto.encrypt_data(ip, context=str(sid))

        ua_index = self._ua_index(ua)
        ua_encrypted = self._encrypt_ua(ua, str(sid))

        max_sessions = sessions_config.get("max_per_user", 10)
        sessions = self.get_sessions(user_id)
        if len(sessions) >= max_sessions:
            sessions.sort(key=lambda x: x.last_activity)
            to_revoke = len(sessions) - max_sessions + 1
            for i in range(to_revoke):
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?",
                    (sessions[i].id,),
                )
            # Evicted sessions must not remain verifiable via the verify cache.
            invalidate_pattern("token_verify:*")

        self._db.execute(
            "INSERT INTO auth_sessions (id, user_id, token_hash, device_id, ip_index, ip_encrypted, ua_index, ua_encrypted, created_at, expires_at, last_activity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                user_id,
                token_hash,
                device_id,
                ip_index,
                ip_encrypted,
                ua_index,
                ua_encrypted,
                now,
                expires,
                now,
            ),
        )
        return Session(
            id=sid,
            user_id=user_id,
            device_id=device_id,
            ip_address=ip,
            user_agent=ua,
            created_at=now,
            expires_at=expires,
            last_activity=now,
            token=token,
        )

    @cached(
        ttl=10,
        prefix="token_verify",
        # Never embed the raw bearer token in the cache key: hash it so that
        # anyone with cache visibility cannot recover live session tokens.
        key_builder=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: (
            f"token_verify:str:{hashlib.sha256(token.encode()).hexdigest()}"
        ),
        skip_cache_if=lambda self, token, ip_address=None, user_agent=None, is_selftest=False: (
            (
                # Single-use WS tickets must NEVER be served from cache: a replay
                # within the TTL would bypass the atomic consumption below.
                isinstance(token, str) and token.startswith("wst.")
            )
            or bool(
                self._config.get("security", {}).get("token_binding", False)
                or self._config.get("security", {}).get("token_verify_rate_limit")
            )
        ),
    )
    def verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        is_selftest: bool = False,
    ) -> TokenInfo:
        security_cfg = self._config.get("security", {})
        rate_limit = security_cfg.get("token_verify_rate_limit")
        if rate_limit and ip_address and not is_selftest:
            from src.core.database.cache import check_rate_limit

            allowed, _ = check_rate_limit(
                key=f"token_verify:{ip_address}",
                limit=int(rate_limit),
                window_seconds=60,
            )
            if not allowed:
                raise TokenInvalidError("Token verification rate limit exceeded")

        parsed = parse_token(token)
        if not parsed:
            raise TokenInvalidError("Invalid token")
        if parsed["token_type"] == "session":
            return self._verify_session_token(parsed, ip_address, user_agent=user_agent)
        elif parsed["token_type"] == "bot":
            return self._verify_bot_token(parsed, ip_address)
        elif parsed["token_type"] == "wst":
            return self._verify_ws_ticket(parsed, ip_address, user_agent=user_agent)
        raise TokenInvalidError("Unknown type")

    def _verify_session_token(
        self, parsed: Dict, ip_address: Optional[str], user_agent: Optional[str] = None
    ) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT s.*, u.username, u.permissions, u.account_type, u.account_locked, u.force_username_change, u.deletion_status FROM auth_sessions s JOIN auth_users u ON s.user_id = u.id WHERE s.id = ?",
            (parsed["id"],),
        )
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid/Revoked")

        if row.get("deletion_status") == "frozen":
            logger.warning(f"Attempted use of token for frozen user {row['user_id']}")
            raise TokenInvalidError("Account is scheduled for deletion")

        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Expired")

        if self._config.get("security", {}).get("token_binding", False):
            if not ip_address:
                raise TokenInvalidError("IP Binding Required")

            current_ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            if row.get("ip_index") and row["ip_index"] != current_ip_index:
                raise TokenInvalidError("IP Binding Mismatch")

            if not user_agent:
                raise TokenInvalidError("User-Agent Binding Required")

            current_ua_index = self._ua_index(user_agent)
            if row.get("ua_index") and row["ua_index"] != current_ua_index:
                raise TokenInvalidError("User-Agent Binding Mismatch")

        now = self._get_timestamp()

        last_activity = row.get("last_activity", 0)
        should_update = (now - last_activity) > 60000
        sessions_config = self._config.get("sessions", {})
        if sessions_config.get("extend_on_activity", True):
            extend_threshold_hours = sessions_config.get("extend_threshold_hours", 24)
            extend_threshold = extend_threshold_hours * 3600 * 1000
            if (
                extend_threshold_hours == 0
                or (row["expires_at"] - now) <= extend_threshold
            ):
                should_update = True

        if should_update:
            updates = ["last_activity = ?"]
            params = [now]

            if sessions_config.get("extend_on_activity", True):
                extend_threshold_hours = sessions_config.get(
                    "extend_threshold_hours", 24
                )
                # Trusted-device sessions re-arm to their own (longer)
                # duration with their own threshold. Fallback chain:
                # trusted_extend_threshold_hours -> trusted_expire_hours ->
                # the untrusted defaults.
                trusted = bool(
                    row.get("device_id")
                    and self._device_is_trusted_by_id(row["user_id"], row["device_id"])
                )
                if trusted:
                    if (
                        sessions_config.get("trusted_extend_threshold_hours")
                        is not None
                    ):
                        extend_threshold_hours = int(
                            sessions_config["trusted_extend_threshold_hours"]
                        )
                    elif sessions_config.get("trusted_expire_hours") is not None:
                        extend_threshold_hours = int(
                            sessions_config["trusted_expire_hours"]
                        )
                extend_threshold = extend_threshold_hours * 3600 * 1000
                if (
                    extend_threshold_hours == 0
                    or (row["expires_at"] - now) <= extend_threshold
                ):
                    if (
                        trusted
                        and sessions_config.get("trusted_expire_hours") is not None
                    ):
                        expire_hours = int(sessions_config["trusted_expire_hours"])
                    else:
                        expire_hours = sessions_config.get("expire_hours", 720)
                    new_expires = now + (expire_hours * 3600 * 1000)
                    updates.append("expires_at = ?")
                    params.append(new_expires)

            params.append(row["id"])
            self._db.execute(
                f"UPDATE auth_sessions SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

        rate_limit_tier = row.get("rate_limit_tier", "standard")
        return TokenInfo(
            valid=True,
            token_type="user",
            account_id=row["user_id"],
            user_id=row["user_id"],
            session_id=row["id"],
            username=row["username"],
            permissions=permissions_from_json(row["permissions"]),
            account_type=AccountType(row["account_type"]),
            rate_limit_tier=rate_limit_tier,
            expires_at=row["expires_at"],
            account_locked=bool(row.get("account_locked", 0)),
            force_username_change=bool(row.get("force_username_change", 0)),
            deletion_status=row.get("deletion_status", "active"),
        )

    def _verify_bot_token(self, parsed: Dict, ip_address: Optional[str]) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT * FROM auth_bots WHERE id = ?", (parsed["id"],)
        )
        if (
            not row
            or row["disabled"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid Bot")

        return TokenInfo(
            valid=True,
            token_type="bot",
            account_id=row["id"],
            user_id=row["owner_id"],
            session_id=None,
            username=row["username"],
            permissions=permissions_from_json(row["permissions"]),
            account_type=AccountType.BOT,
            rate_limit_tier="bot",
            expires_at=None,
        )

    # ------------------------------------------------------------------
    # WebSocket gateway tickets
    #
    # Short-lived, single-use credentials (``wst.{id}.{secret}``) minted for
    # an authenticated session so the long-lived bearer token never has to be
    # handed to the gateway handshake.
    # ------------------------------------------------------------------

    WS_TICKET_TTL_MS = 60 * 1000

    def create_ws_ticket_for_session(
        self,
        user_id: int,
        session_id: Optional[int],
    ) -> Tuple[str, int]:
        """Mint a single-use gateway ticket bound to ``session_id``.

        Returns ``(ticket_token, expires_in_seconds)``.
        """
        self._ensure_ws_tickets_table()
        tid = self._generate_id()
        now = self._get_timestamp()
        ttl_seconds = int(self.WS_TICKET_TTL_MS // 1000)
        token, token_hash = create_ws_ticket(tid)
        self._db.execute(
            "INSERT INTO auth_ws_tickets (id, user_id, session_id, ticket_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                tid,
                user_id,
                session_id,
                token_hash,
                now,
                now + self.WS_TICKET_TTL_MS,
            ),
        )
        return token, ttl_seconds

    _ws_tickets_table_ready: bool = False

    def _ensure_ws_tickets_table(self) -> None:
        if LifecycleMixin._ws_tickets_table_ready:
            return
        from ...schema import ensure_ws_tickets_table

        ensure_ws_tickets_table(self._db)
        LifecycleMixin._ws_tickets_table_ready = True

    def _verify_ws_ticket(
        self,
        parsed: Dict,
        ip_address: Optional[str],
        user_agent: Optional[str] = None,
    ) -> TokenInfo:
        row = self._db.fetch_one(
            "SELECT * FROM auth_ws_tickets WHERE id = ?", (parsed["id"],)
        )
        if not row or not verify_token_hash(parsed["secret"], row["ticket_hash"]):
            raise TokenInvalidError("Invalid ticket")
        if row["used"]:
            raise TokenInvalidError("Ticket already used")
        if row["expires_at"] < self._get_timestamp():
            raise TokenExpiredError("Ticket expired")

        # Atomic single-use consumption: concurrent replays of the same
        # ticket cannot both pass this gate.
        cursor = self._db.execute(
            "UPDATE auth_ws_tickets SET used = 1 WHERE id = ? AND used = 0",
            (row["id"],),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            logger.warning(f"WS ticket replay detected (id={row['id']})")
            raise TokenInvalidError("Ticket already used")

        srow = (
            self._db.fetch_one(
                "SELECT s.*, u.username, u.permissions, u.account_type, u.account_locked, "
                "u.force_username_change, u.deletion_status "
                "FROM auth_sessions s JOIN auth_users u ON s.user_id = u.id WHERE s.id = ?",
                (row["session_id"],),
            )
            if row["session_id"]
            else None
        )
        if (
            not srow
            or srow["revoked"]
            or srow["expires_at"] < self._get_timestamp()
            or srow.get("deletion_status") == "frozen"
        ):
            raise TokenInvalidError("Ticket session no longer valid")

        return TokenInfo(
            valid=True,
            token_type="user",
            account_id=srow["user_id"],
            user_id=srow["user_id"],
            session_id=srow["id"],
            username=srow["username"],
            permissions=permissions_from_json(srow["permissions"]),
            account_type=AccountType(srow["account_type"]),
            rate_limit_tier=srow.get("rate_limit_tier", "standard"),
            expires_at=srow["expires_at"],
            account_locked=bool(srow.get("account_locked", 0)),
            force_username_change=bool(srow.get("force_username_change", 0)),
            deletion_status=srow.get("deletion_status", "active"),
        )

    def refresh_session(self, token: str) -> Optional[str]:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "session":
            return None
        row = self._db.fetch_one(
            "SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],)
        )
        # The session secret must be verified, not just the (enumerable) ID,
        # otherwise anyone who learns a session ID can extend it forever.
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            return None
        if row["expires_at"] < self._get_timestamp():
            return None
        now = self._get_timestamp()
        # Trusted-device sessions must keep their longer TTL across a refresh:
        # resolving the bare ``expire_hours`` here silently shortened a (say)
        # 30-day trusted session back to the untrusted default on every
        # refresh, so the trusted duration could never be reached.
        sessions_config = self._config.get("sessions", {})
        expire_hours = self._session_expire_hours(
            row["user_id"], row.get("device_id"), sessions_config
        )
        new_expires = now + (expire_hours * 3600 * 1000)
        # Token rotation (F3): mint a fresh secret so an intercepted token
        # is only valid until the next refresh. Old token is invalidated
        # immediately by replacing token_hash; client storage must be updated
        # atomically (caller sets both bearer and cookie to the new token).
        new_token, new_hash = create_session_token(parsed["id"])
        self._db.execute(
            "UPDATE auth_sessions SET token_hash = ?, expires_at = ?, last_activity = ? WHERE id = ?",
            (new_hash, new_expires, now, parsed["id"]),
        )
        invalidate_pattern("token_verify:*")
        return new_token

    def logout(self, token: str) -> bool:
        parsed = parse_token(token)
        if parsed and parsed["token_type"] == "session":
            row = self._db.fetch_one(
                "SELECT user_id, token_hash FROM auth_sessions WHERE id = ?",
                (parsed["id"],),
            )
            # The session secret must be verified, not just the (enumerable)
            # ID, otherwise anyone who learns a session ID can revoke that
            # session without ever holding its token. Mirrors refresh_session.
            if row and verify_token_hash(parsed["secret"], row["token_hash"]):
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?", (parsed["id"],)
                )
                # Invalidate after the write so a concurrent verify_token cannot
                # re-cache the still-valid row in between.
                invalidate_pattern("token_verify:*")
                self._log_audit(AuditEventType.LOGOUT, row["user_id"], True)
                return True
        return False

    def logout_all(self, user_id: int, except_token: Optional[str] = None) -> int:
        invalidate_pattern("token_verify:*")

        if except_token:
            parsed = parse_token(except_token)
            if parsed and parsed["token_type"] == "session":
                count_row = self._db.fetch_one(
                    "SELECT COUNT(*) as cnt FROM auth_sessions WHERE user_id = ? AND id != ? AND revoked = 0",
                    (user_id, parsed["id"]),
                )
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND id != ?",
                    (user_id, parsed["id"]),
                )
                try:
                    from src.core.events.gateway_emit import emit_security_alert

                    emit_security_alert(user_id, "session_revoked")
                except Exception:
                    pass
                return count_row["cnt"] if count_row else 0
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM auth_sessions WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "session_revoked")
        except Exception:
            pass
        return count_row["cnt"] if count_row else 0

    def logout_all_users(self) -> int:
        invalidate_pattern("token_verify:*")
        self._db.execute("UPDATE auth_sessions SET revoked = 1 WHERE revoked = 0")
        logger.info("Site-wide session purge: all users logged out")
        return 1

    def get_sessions(self, user_id: int) -> List[Session]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_sessions WHERE user_id = ? AND revoked = 0", (user_id,)
        )
        sessions = []
        for r in rows:
            ip_address = None
            if r.get("ip_encrypted"):
                try:
                    ip_address = self.crypto.decrypt_data(
                        r["ip_encrypted"], context=str(r["id"])
                    )
                except Exception:
                    ip_address = "[decryption failed]"

            ua = None
            if r.get("ua_encrypted"):
                try:
                    ua = self.crypto.decrypt_data(
                        r["ua_encrypted"], context=str(r["id"])
                    )
                except Exception:
                    ua = "[decryption failed]"

            sessions.append(
                Session(
                    id=r["id"],
                    user_id=r["user_id"],
                    device_id=r["device_id"],
                    ip_address=ip_address,
                    user_agent=ua,
                    created_at=r["created_at"],
                    expires_at=r["expires_at"],
                    last_activity=r["last_activity"],
                    revoked=bool(r["revoked"]),
                )
            )
        return sessions

    def revoke_session(self, user_id: int, session_id: int) -> bool:
        cursor = self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        # Invalidate after the write so a concurrent verify_token cannot
        # re-cache the still-valid row in between.
        invalidate_pattern("token_verify:*")
        if cursor.rowcount > 0:
            self._log_audit(AuditEventType.SESSION_REVOKED, user_id, True)
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(user_id, "session_revoked")
            except Exception:
                pass
            return True
        return False
