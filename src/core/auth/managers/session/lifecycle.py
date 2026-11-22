"""Session lifecycle: create, extend, refresh, revoke and list sessions."""

import hmac
from typing import List, Optional

import utils.logger as logger
from src.core.database import invalidate_pattern

from ...gateway_revocation import (
    close_session_gateway_connections,
    close_user_gateway_connections,
)
from ...exceptions import TokenExpiredError, TokenInvalidError
from ...models import AuditEventType, Session
from ...replay import refresh_replay_guard
from ...tokens import (
    create_session_token,
    hash_token,
    is_expired,
    is_malformed_token_input,
    parse_token,
    safe_token_cache_key,
    verify_token_hash,
)
from ..protocol import AuthManagerProtocol


from typing import Dict, Tuple
from src.core.database import cached
from ...models import AccountType, TokenInfo
from ...permissions import has_permission, permissions_from_json
from ...tokens import create_ws_ticket


# Bounded IN chunk for cross-user session reads (see get_sessions_bulk). Under
# SQLite's classic 999-parameter ceiling and PostgreSQL's limit alike, 500
# binds per query keeps the admin overview's fan-out constant in the user set.
_SESSION_BULK_IN_CHUNK = 500


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

        # Enforce max_per_user AFTER writing our own row. A pre-insert
        # COUNT-then-evict is a check/use race: two concurrent logins both see
        # a free slot and both insert, leaving cap+1 live sessions (and, with
        # more racers, an unbounded number). Re-counting after our insert and
        # revoking exactly the excess in ONE guarded statement makes every
        # concurrent creator converge on the cap: a racer that already evicted
        # leaves nothing to evict for the next one, and the ``revoked = 0``
        # predicate stops two racers double-revoking the same rows. The
        # just-created session is excluded so it can never be evicted by a
        # stale last_activity value. This also avoids the old full
        # get_sessions() fetch that decrypted every stored IP/UA just to sort
        # by last_activity.
        max_sessions = sessions_config.get("max_per_user", 10)
        try:
            max_sessions = max(1, int(max_sessions))
        except (TypeError, ValueError):
            max_sessions = 10
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) AS c FROM auth_sessions WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )
        active_count = int(count_row["c"]) if count_row else 0
        excess = active_count - max_sessions
        if excess > 0:
            # Capture the eviction candidates before the guarded UPDATE. The
            # single-statement eviction below is deliberately kept (it makes
            # concurrent logins converge on the cap); after the write we close
            # the sockets of the rows that actually became revoked, so a
            # concurrently created/kept session is never targeted.
            evicted_candidates = self._db.fetch_all(
                "SELECT id FROM auth_sessions WHERE user_id = ? AND revoked = 0 "
                "AND id != ? ORDER BY last_activity ASC, id ASC LIMIT ?",
                (user_id, sid, excess),
            )
            self._db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE id IN ("
                "SELECT id FROM auth_sessions WHERE user_id = ? AND revoked = 0 "
                "AND id != ? ORDER BY last_activity ASC, id ASC LIMIT ?)",
                (user_id, sid, excess),
            )
            # Evicted sessions must not remain verifiable via the verify cache.
            invalidate_pattern("token_verify:*")
            for candidate in evicted_candidates:
                evicted_id = int(candidate["id"])
                row = self._db.fetch_one(
                    "SELECT revoked FROM auth_sessions WHERE id = ?", (evicted_id,)
                )
                if row and row["revoked"]:
                    # An evicted session is dead; its socket must not stay
                    # authenticated. Target only that auth session.
                    close_session_gateway_connections(
                        user_id, evicted_id, "Session revoked"
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

    def verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        is_selftest: bool = False,
    ) -> TokenInfo:
        if is_malformed_token_input(token):
            raise TokenInvalidError("Invalid token")
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

        result = self._verify_token(token, ip_address, user_agent)
        # N712 parity: re-check the cached verdict's expiry on every call.
        expires_at = getattr(result, "expires_at", None)
        if expires_at is not None and is_expired(expires_at):
            raise TokenExpiredError("Expired")
        return result

    @cached(
        ttl=10,
        prefix="token_verify",
        # Never embed the raw bearer token in the cache key: hash it so that
        # anyone with cache visibility cannot recover live session tokens.
        key_builder=lambda self, token, ip_address=None, user_agent=None: (
            safe_token_cache_key(token)
        ),
        skip_cache_if=lambda self, token, ip_address=None, user_agent=None: (
            is_malformed_token_input(token)
            or (
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
    def _verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenInfo:
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
        if parsed.get("token_type") != "session":
            raise TokenInvalidError("Token type mismatch")
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

        if is_expired(row["expires_at"], self._get_timestamp()):
            raise TokenExpiredError("Expired")

        if self._config.get("security", {}).get("token_binding", False):
            if not ip_address:
                raise TokenInvalidError("IP Binding Required")

            # SECURITY: binding is MANDATORY when enabled. Guarding the
            # comparison on ``row.get("ip_index")``/``row.get("ua_index")`` let a
            # session with no stored index (minted with no IP/UA, a legacy row,
            # or a failed index write) skip the comparison entirely, so a stolen
            # bearer token could be replayed from any address. Missing/corrupt
            # binding material now fails closed and comparisons are constant
            # time, mirroring the hardened live verifier in VerificationMixin.
            stored_ip_index = row.get("ip_index")
            if not isinstance(stored_ip_index, str) or not stored_ip_index:
                raise TokenInvalidError("IP Binding Required")
            current_ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            if not hmac.compare_digest(stored_ip_index, current_ip_index):
                raise TokenInvalidError("IP Binding Mismatch")

            if not user_agent:
                raise TokenInvalidError("User-Agent Binding Required")

            stored_ua_index = row.get("ua_index")
            if not isinstance(stored_ua_index, str) or not stored_ua_index:
                raise TokenInvalidError("User-Agent Binding Required")
            current_ua_index = self._ua_index(user_agent)
            if not isinstance(current_ua_index, str) or not current_ua_index:
                raise TokenInvalidError("User-Agent Binding Required")
            if not hmac.compare_digest(stored_ua_index, current_ua_index):
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
        if parsed.get("token_type") != "bot":
            raise TokenInvalidError("Token type mismatch")
        row = self._db.fetch_one(
            "SELECT * FROM auth_bots WHERE id = ?", (parsed["id"],)
        )
        if (
            not row
            or row["disabled"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            raise TokenInvalidError("Invalid Bot")

        # Mirror of VerificationMixin._verify_bot_token's delegation ceiling,
        # kept in sync so a future MRO reorder or a direct caller of this
        # exported mixin cannot reintroduce the escalation. A bot's permission
        # map is a DELEGATION of its owner's authority: a scope legitimately
        # granted while the owner held it must not survive a later demotion.
        # Intersect with the owner's CURRENT grants on every verification and
        # fail closed when the owner row is missing or its payload is not a
        # well-formed permission map.
        owner_row = self._db.fetch_one(
            "SELECT permissions FROM auth_users WHERE id = ?", (row["owner_id"],)
        )
        if not owner_row:
            raise TokenInvalidError("Invalid Bot")
        owner_perms = permissions_from_json(owner_row["permissions"])
        bot_perms = permissions_from_json(row["permissions"])
        if not isinstance(owner_perms, dict) or not isinstance(bot_perms, dict):
            raise TokenInvalidError("Invalid Bot")
        effective_perms = {
            perm: allowed
            for perm, allowed in bot_perms.items()
            if not allowed or has_permission(owner_perms, perm)
        }

        return TokenInfo(
            valid=True,
            token_type="bot",
            account_id=row["id"],
            user_id=row["owner_id"],
            session_id=None,
            username=row["username"],
            permissions=effective_perms,
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

    # Mirror of TicketMixin's bounded store: prune expired tickets on the mint
    # path so this historical copy cannot be used to grow auth_ws_tickets
    # without bound either (see TicketMixin._prune_expired_ws_tickets).
    _last_ws_ticket_cleanup: int = 0
    _ws_ticket_cleanup_interval_ms: int = 300_000

    def _prune_expired_ws_tickets(self, now: int) -> None:
        """Best-effort purge of expired gateway tickets (rate-limited)."""
        if (
            now - LifecycleMixin._last_ws_ticket_cleanup
            < LifecycleMixin._ws_ticket_cleanup_interval_ms
        ):
            return
        LifecycleMixin._last_ws_ticket_cleanup = now
        try:
            self._db.execute(
                "DELETE FROM auth_ws_tickets WHERE expires_at <= ?", (now,)
            )
        except Exception as exc:
            logger.debug(f"WS ticket prune failed: {exc}")

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
        self._prune_expired_ws_tickets(now)
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
        if parsed.get("token_type") != "wst":
            raise TokenInvalidError("Token type mismatch")
        row = self._db.fetch_one(
            "SELECT * FROM auth_ws_tickets WHERE id = ?", (parsed["id"],)
        )
        if not row or not verify_token_hash(parsed["secret"], row["ticket_hash"]):
            raise TokenInvalidError("Invalid ticket")
        if row["used"]:
            raise TokenInvalidError("Ticket already used")
        if is_expired(row["expires_at"], self._get_timestamp()):
            raise TokenExpiredError("Ticket expired")

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
            or is_expired(srow["expires_at"], self._get_timestamp())
            or srow.get("deletion_status") == "frozen"
        ):
            raise TokenInvalidError("Ticket session no longer valid")

        # A gateway ticket is a short-lived bearer derived from an auth
        # session, and it is the only credential the gateway handshake ever
        # sees. With token binding enabled the handshake must carry the same
        # IP/User-Agent as the session the ticket was minted for: otherwise a
        # stolen ticket can be exchanged into an authenticated socket from any
        # host, laundering away the binding the live session verifier enforces.
        # Keep this mirror of TicketMixin._verify_ws_ticket in sync so a future
        # MRO reorder or a direct caller cannot bypass the check. Compare raw
        # presented forms symmetrically with session mint/verification
        # (blind_index lower/strip is the only transformation on both sides);
        # canonicalizing only this side would not match stored raw indexes.
        # Fail closed on missing or corrupt binding material, matching
        # _verify_session_token.
        if self._config.get("security", {}).get("token_binding", False):
            if not ip_address:
                raise TokenInvalidError("IP Binding Required")
            stored_ip_index = srow.get("ip_index")
            if not isinstance(stored_ip_index, str) or not stored_ip_index:
                raise TokenInvalidError("IP Binding Required")
            if not hmac.compare_digest(
                stored_ip_index,
                self.crypto.fast_blind_index(ip_address, "ip_address"),
            ):
                raise TokenInvalidError("IP Binding Mismatch")

            if not user_agent:
                raise TokenInvalidError("User-Agent Binding Required")
            stored_ua_index = srow.get("ua_index")
            if not isinstance(stored_ua_index, str) or not stored_ua_index:
                raise TokenInvalidError("User-Agent Binding Required")
            current_ua_index = self._ua_index(user_agent)
            if not isinstance(current_ua_index, str) or not current_ua_index:
                raise TokenInvalidError("User-Agent Binding Required")
            if not hmac.compare_digest(stored_ua_index, current_ua_index):
                raise TokenInvalidError("User-Agent Binding Mismatch")

        # Atomic single-use consumption: concurrent replays of the same
        # ticket cannot both pass this gate. Kept after all validation so a
        # rejected context does not burn a ticket a legitimate client needs.
        cursor = self._db.execute(
            "UPDATE auth_ws_tickets SET used = 1 WHERE id = ? AND used = 0",
            (row["id"],),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            logger.warning(f"WS ticket replay detected (id={row['id']})")
            raise TokenInvalidError("Ticket already used")

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

    def _revoke_for_refresh_replay(self, session_id: int, row) -> None:
        """Kill a session whose pre-rotation bearer was presented twice."""
        logger.warning(
            f"Refresh token replay detected for session {session_id}; revoking"
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE id = ?", (session_id,)
        )
        invalidate_pattern("token_verify:*")
        if row:
            self._log_audit(
                AuditEventType.LOGOUT,
                row["user_id"],
                False,
                details={"reason": "refresh_token_replay"},
            )
            # A replayed pre-rotation bearer means the credential leaked. The
            # session row is revoked above, but a socket authenticated under it
            # stays live server-side unless we close it too. Target only this
            # auth session so the user's other valid sessions keep working.
            close_session_gateway_connections(
                row["user_id"], session_id, "Session revoked"
            )

    def _refresh_binding_ok(self, row, ip_address, user_agent) -> bool:
        """Fail-closed IP/UA binding check shared by refresh rotation and by
        the replay-revocation branch. Both paths consume a presented bearer and
        both must reject a valid-secret-but-wrong-context credential before it
        can change state; keeping one implementation prevents them drifting
        (the recurring mirror-parity theme in this codebase).

        Missing/corrupt binding material fails closed just as it does in the
        live verifier -- never silently skip the comparison when binding is on.
        """
        if not self._config.get("security", {}).get("token_binding", False):
            return True
        if not ip_address:
            return False
        stored_ip_index = row.get("ip_index") if row else None
        if not isinstance(stored_ip_index, str) or not stored_ip_index:
            return False
        if not hmac.compare_digest(
            stored_ip_index, self.crypto.fast_blind_index(ip_address, "ip_address")
        ):
            return False
        if not user_agent:
            return False
        stored_ua_index = row.get("ua_index") if row else None
        if not isinstance(stored_ua_index, str) or not stored_ua_index:
            return False
        current_ua_index = self._ua_index(user_agent)
        if not isinstance(current_ua_index, str) or not current_ua_index:
            return False
        return hmac.compare_digest(stored_ua_index, current_ua_index)

    def refresh_session(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[str]:
        parsed = parse_token(token)
        if not parsed or parsed["token_type"] != "session":
            return None
        row = self._db.fetch_one(
            "SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],)
        )
        # Replay guard: a token that was already rotated away must never be
        # accepted a second time. Reuse means the pre-rotation bearer leaked,
        # so revoke the whole session instead of silently returning None.
        # N720 follow-up: this branch sits BEFORE the normal secret/context
        # checks (a rotated token's secret no longer matches the live row), so
        # it must re-verify the presented IP/UA itself. Previously it revoked on
        # a valid-secret-but-wrong-context bearer without ever checking the
        # binding, i.e. the same stale-context availability DoS N720 closed in
        # logout(). Replay from the bound context still revokes (leak detection
        # is intact); a wrong-context or unbound attempt fails closed and
        # changes nothing.
        rotated_key = f"refresh:{parsed['id']}:{hash_token(parsed['secret'])}"
        if refresh_replay_guard.contains(rotated_key):
            if not self._refresh_binding_ok(row, ip_address, user_agent):
                return None
            self._revoke_for_refresh_replay(parsed["id"], row)
            return None
        # The session secret must be verified, not just the (enumerable) ID,
        # otherwise anyone who learns a session ID can extend it forever.
        if (
            not row
            or row["revoked"]
            or not verify_token_hash(parsed["secret"], row["token_hash"])
        ):
            return None
        if is_expired(row["expires_at"], self._get_timestamp()):
            return None
        # N644: verify the PRESENTED refresh credential's context against the
        # session's bound indexes, mirroring the fail-closed gate in
        # VerificationMixin._verify_session_token (and this mixin's verifier).
        # Verification of the *new* token downstream did not protect this
        # rotation: a stolen refresh bearer used from another host could force a
        # rotation, retiring the victim's current credential via the atomic
        # consume() below. The victim's next refresh would then trip replay-based
        # revocation (availability) even though the attacker could not use the
        # new token from that host. Deny BEFORE consume() so a stolen-context
        # attempt changes nothing and leaves the victim's session intact.
        # Shared with the replay-revocation branch above via _refresh_binding_ok.
        if not self._refresh_binding_ok(row, ip_address, user_agent):
            return None
        # Atomically claim the still-current token. ``contains`` above detects
        # already-rotated tokens whose hash no longer matches the live row;
        # this consume closes the check-then-add race where two concurrent
        # refreshes present the SAME valid token and both rotate it. Without
        # it both pass the replay check and both hand out a usable new
        # credential, and neither is flagged as a replay.
        if not refresh_replay_guard.consume(rotated_key):
            self._revoke_for_refresh_replay(parsed["id"], row)
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
        # The atomic consume() above already recorded the pre-rotation token,
        # so a later replay is detectable even though its hash no longer
        # matches the live session row.
        invalidate_pattern("token_verify:*")
        return new_token

    def logout(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> bool:
        parsed = parse_token(token)
        if parsed and parsed["token_type"] == "session":
            row = self._db.fetch_one(
                "SELECT user_id, token_hash, ip_index, ua_index FROM auth_sessions WHERE id = ?",
                (parsed["id"],),
            )
            # The session secret must be verified, not just the (enumerable)
            # ID, otherwise anyone who learns a session ID can revoke that
            # session without ever holding its token. Mirrors refresh_session.
            if row and verify_token_hash(parsed["secret"], row["token_hash"]):
                # N644 follow-up: a session bearer that is cryptographically
                # valid but presented from the wrong host/user-agent must not be
                # able to revoke the victim's session. With binding enabled the
                # live verifier rejects such a bearer, so allowing it to drive
                # this token-consuming write is the same stale-context
                # availability DoS refresh_session was hardened against. Fail
                # closed before the revoke on a mismatch OR missing/corrupt
                # binding material, matching _verify_session_token.
                if self._config.get("security", {}).get("token_binding", False):
                    if not ip_address:
                        return False
                    stored_ip_index = row.get("ip_index")
                    if not isinstance(stored_ip_index, str) or not stored_ip_index:
                        return False
                    current_ip_index = self.crypto.fast_blind_index(
                        ip_address, "ip_address"
                    )
                    if not hmac.compare_digest(stored_ip_index, current_ip_index):
                        return False
                    if not user_agent:
                        return False
                    stored_ua_index = row.get("ua_index")
                    if not isinstance(stored_ua_index, str) or not stored_ua_index:
                        return False
                    current_ua_index = self._ua_index(user_agent)
                    if not isinstance(current_ua_index, str) or not current_ua_index:
                        return False
                    if not hmac.compare_digest(stored_ua_index, current_ua_index):
                        return False
                self._db.execute(
                    "UPDATE auth_sessions SET revoked = 1 WHERE id = ?", (parsed["id"],)
                )
                # Invalidate after the write so a concurrent verify_token cannot
                # re-cache the still-valid row in between. Scope the drop to the
                # credential being revoked: verify verdicts are keyed per token
                # (``safe_token_cache_key`` + an IP/UA binding suffix), and this
                # logout revokes exactly one session. The trailing ``*`` clears
                # every binding variant of that token. Flushing the whole
                # ``token_verify:*`` keyspace here would drop every other
                # authenticated user's memoised TokenInfo on one account's
                # logout, forcing a site-wide DB re-verification thundering herd.
                invalidate_pattern(f"{safe_token_cache_key(token)}*")
                self._log_audit(AuditEventType.LOGOUT, row["user_id"], True)
                # The session is revoked; any socket authenticated under it is
                # no longer legitimate, even though gateway auth happens only
                # at IDENTIFY/RESUME. Close just this auth session's sockets so
                # a stolen token used for logout cannot be the only thing that
                # drops the socket, and sibling sessions are unaffected.
                close_session_gateway_connections(
                    row["user_id"], parsed["id"], "Session revoked"
                )
                return True
        return False

    def logout_all(self, user_id: int, except_token: Optional[str] = None) -> int:
        # NOTE: do NOT invalidate ``token_verify:*`` here. Every sibling
        # revocation path (``logout``, ``revoke_session``,
        # ``revoke_sessions_bulk``, ``revoke_device``) drops the verdict cache
        # AFTER its write, because a verification landing between an early
        # invalidation and the revocation re-caches the still-live TokenInfo and
        # keeps it authenticating for the rest of the verify TTL. The
        # invalidation is performed immediately after each UPDATE below.

        # ``except_token`` decides which live session SURVIVES a mass
        # revocation, so it is a credential and must be treated as one. Every
        # other token-consuming path here (``logout``, ``refresh_session``,
        # ``_verify_ws_ticket``) verifies the presented secret against the
        # stored hash before acting. This path previously used only the
        # *unverified* id parsed from the token, letting anyone who can
        # influence the argument keep an arbitrary session id alive by forging
        # ``<that id>.<any secret>``. Verify both the secret and that the
        # session actually belongs to ``user_id`` before honouring the
        # exception; a forged/mismatched token fails closed to "revoke all".
        except_session_id: Optional[int] = None
        if except_token:
            parsed = parse_token(except_token)
            if parsed and parsed["token_type"] == "session":
                keep_row = self._db.fetch_one(
                    "SELECT user_id, token_hash FROM auth_sessions WHERE id = ?",
                    (parsed["id"],),
                )
                if (
                    keep_row
                    and keep_row["user_id"] == user_id
                    and verify_token_hash(parsed["secret"], keep_row["token_hash"])
                ):
                    except_session_id = parsed["id"]
                else:
                    logger.warning(
                        "logout_all: rejected unverified except_token for user %s",
                        user_id,
                    )

        if except_session_id is not None:
            count_row = self._db.fetch_one(
                "SELECT COUNT(*) as cnt FROM auth_sessions WHERE user_id = ? AND id != ? AND revoked = 0",
                (user_id, except_session_id),
            )
            self._db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND id != ?",
                (user_id, except_session_id),
            )
            # Invalidate after the write so a concurrent verify_token cannot
            # re-cache the still-valid row in between (see the note above).
            invalidate_pattern("token_verify:*")
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(user_id, "session_revoked")
            except Exception as exc:
                # Sessions are already revoked; the alert is advisory.
                logger.debug(f"session_revoked alert not emitted: {exc}")
            # A SECURITY_ALERT frame only asks the client to leave; close the
            # live socket server-side too (fail closed for every caller of
            # logout_all -- lock/disable, force-logout, deletion, reaper).
            close_user_gateway_connections(user_id, "All sessions revoked")
            return count_row["cnt"] if count_row else 0
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM auth_sessions WHERE user_id = ? AND revoked = 0",
            (user_id,),
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?", (user_id,)
        )
        # Invalidate after the write so a concurrent verify_token cannot
        # re-cache the still-valid row in between (see the note above).
        invalidate_pattern("token_verify:*")
        try:
            from src.core.events.gateway_emit import emit_security_alert

            emit_security_alert(user_id, "session_revoked")
        except Exception as exc:
            logger.debug(f"session_revoked alert not emitted: {exc}")
        # See the except-branch above: the frame is a hint, the close is the
        # guarantee. Covers the account-level revocation callers of logout_all.
        close_user_gateway_connections(user_id, "All sessions revoked")
        return count_row["cnt"] if count_row else 0

    def logout_all_users(self) -> int:
        self._db.execute("UPDATE auth_sessions SET revoked = 1 WHERE revoked = 0")
        # Invalidate after the write so a concurrent verify_token cannot
        # re-cache a still-valid row in between (as in logout_all above).
        invalidate_pattern("token_verify:*")
        logger.info("Site-wide session purge: all users logged out")
        return 1

    def _session_from_row(self, r) -> Session:
        """Build one (live, non-revoked) Session from a row.

        Shared by get_sessions and get_sessions_bulk so batching cannot drift
        from the single-user path (same fields, same crypto contexts).
        """
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
                ua = self.crypto.decrypt_data(r["ua_encrypted"], context=str(r["id"]))
            except Exception:
                ua = "[decryption failed]"

        return Session(
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

    def get_sessions(self, user_id: int) -> List[Session]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_sessions WHERE user_id = ? AND revoked = 0", (user_id,)
        )
        return [self._session_from_row(r) for r in rows]

    def get_sessions_bulk(self, user_ids) -> Dict[int, List[Session]]:
        """Fetch live sessions for many users in bounded IN chunks.

        The revoked predicate is repeated in every chunk, and rows are grouped
        by the row's own ``user_id`` after checking it against the requested
        set. A caller can therefore only receive sessions for users it asked
        for; an unexpected row can never be attributed to a different user.
        No results are cached in-process, so revocations on other workers are
        reflected on the very next call.
        """
        ids = sorted({int(uid) for uid in user_ids})
        by_user: Dict[int, List[Session]] = {uid: [] for uid in ids}
        if not ids:
            return by_user
        for start in range(0, len(ids), _SESSION_BULK_IN_CHUNK):
            chunk = ids[start : start + _SESSION_BULK_IN_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                "SELECT * FROM auth_sessions "
                f"WHERE user_id IN ({placeholders}) AND revoked = 0",
                tuple(chunk),
            )
            for r in rows:
                uid = int(r["user_id"])
                if uid not in by_user:
                    continue  # fail closed: never group an unrequested row
                by_user[uid].append(self._session_from_row(r))
        return by_user

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
            except Exception as exc:
                logger.debug(f"session_revoked alert not emitted: {exc}")
            # A SECURITY_ALERT is only a client-side hint. Close the live socket(s)
            # bound to this auth session server-side, and only those: the user's
            # other still-valid sessions must keep working. ``rowcount > 0``
            # proves the row belonged to ``user_id`` before we touch any socket.
            close_session_gateway_connections(user_id, session_id, "Session revoked")
            return True
        return False

    def revoke_sessions_bulk(
        self,
        user_id: int,
        session_ids: List[int],
        except_session_id: Optional[int] = None,
    ) -> int:
        """Revoke many of a user's sessions in one write.

        Used by the ``revoke-all`` route, which used to issue one UPDATE, one
        cache invalidation, one audit insert and one security alert *per*
        session. All revocations are now a single ``UPDATE ... IN (...)`` with
        one post-write invalidation, while keeping every security invariant:

        * only rows owned by ``user_id`` are touched (same predicate as
          :meth:`revoke_session`);
        * the token cache is invalidated after the write so a concurrent
          verification cannot re-cache a still-valid row;
        * an audit event and a security alert are still recorded.

        Returns the number of sessions actually revoked.
        """
        targets: List[int] = []
        for session_id in session_ids:
            if session_id is None:
                continue
            try:
                sid = int(session_id)
            except (TypeError, ValueError):
                continue
            if except_session_id is not None and sid == int(except_session_id):
                continue
            targets.append(sid)

        if not targets:
            return 0

        placeholders = ",".join("?" for _ in targets)
        cursor = self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 "
            f"WHERE user_id = ? AND revoked = 0 AND id IN ({placeholders})",
            (user_id, *targets),
        )
        revoked = int(getattr(cursor, "rowcount", 0) or 0)
        if revoked:
            # Invalidate after the write so a concurrent verify_token cannot
            # re-cache a still-valid row in between.
            invalidate_pattern("token_verify:*")
            self._log_audit(
                AuditEventType.SESSION_REVOKED,
                user_id,
                True,
                details={"revoked_count": revoked},
            )
            try:
                from src.core.events.gateway_emit import emit_security_alert

                emit_security_alert(user_id, "session_revoked")
            except Exception as exc:
                logger.debug(f"session_revoked alert not emitted: {exc}")
            # A SECURITY_ALERT is only a client-side hint; the sockets of the
            # revoked sessions stay authenticated server-side unless closed.
            # Close them one by one by auth-session id rather than blanket
            # closing the user: with except_session_id the surviving current
            # session (and its socket) must remain usable. Only sessions that
            # are now actually revoked are targeted.
            now_revoked = self._db.fetch_all(
                "SELECT id FROM auth_sessions WHERE user_id = ? AND revoked = 1 "
                f"AND id IN ({placeholders})",
                (user_id, *targets),
            )
            for row in now_revoked:
                close_session_gateway_connections(
                    user_id, int(row["id"]), "Session revoked"
                )
        return revoked
