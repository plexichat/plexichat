"""Single-use WebSocket gateway tickets."""

import hmac
from typing import Optional, Tuple

from ...exceptions import TokenExpiredError, TokenInvalidError
from ...models import AccountType, TokenInfo
from ...permissions import permissions_from_json
from ...tokens import create_ws_ticket, is_expired, verify_token_hash
from ..protocol import AuthManagerProtocol


from typing import Dict
import utils.logger as logger


class TicketMixin(AuthManagerProtocol):
    # ------------------------------------------------------------------
    # WebSocket gateway tickets
    #
    # Short-lived, single-use credentials (``wst.{id}.{secret}``) minted for
    # an authenticated session so the long-lived bearer token never has to be
    # handed to the gateway handshake.
    # ------------------------------------------------------------------

    WS_TICKET_TTL_MS = 60 * 1000

    # Every gateway (re)connect mints a ticket. Rows used to live forever: the
    # expires_at index existed but nothing ever deleted expired rows, so any
    # authenticated client (or normal reconnects) could grow auth_ws_tickets
    # without bound. Prune lazily on the mint path, rate-limited like the 2FA
    # challenge cleanup so a busy server never pays a full DELETE scan on every
    # connection. The reaper performs the same sweep for idle deployments.
    _last_ws_ticket_cleanup: int = 0
    _ws_ticket_cleanup_interval_ms: int = 300_000

    def _prune_expired_ws_tickets(self, now: int) -> None:
        """Best-effort purge of expired gateway tickets (rate-limited)."""
        if (
            now - TicketMixin._last_ws_ticket_cleanup
            < TicketMixin._ws_ticket_cleanup_interval_ms
        ):
            return
        TicketMixin._last_ws_ticket_cleanup = now
        try:
            self._db.execute(
                "DELETE FROM auth_ws_tickets WHERE expires_at <= ?", (now,)
            )
        except Exception as exc:
            # Pruning is best-effort: expired tickets are rejected on read, so
            # a failure only costs space until the next successful prune.
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
        if TicketMixin._ws_tickets_table_ready:
            return
        from ...schema import ensure_ws_tickets_table

        ensure_ws_tickets_table(self._db)
        TicketMixin._ws_tickets_table_ready = True

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

        # A gateway ticket is a short-lived bearer *derived from* an auth
        # session, and it is the only credential the gateway handshake ever
        # sees. With token binding enabled the handshake must carry the same
        # IP/User-Agent as the session the ticket was minted for: otherwise a
        # stolen ticket can be exchanged into an authenticated socket from any
        # host, laundering away the binding the live session verifier enforces.
        # The socket authenticates only once (IDENTIFY/RESUME), so this is the
        # only place the check can happen. Compare the raw presented form
        # symmetrically with session mint/verification (blind_index lower/strip
        # is the only transformation on both sides); canonicalizing just this
        # side would not match stored raw indexes. Fail closed on missing or
        # corrupt binding material, matching _verify_session_token.
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
