"""Single-use WebSocket gateway tickets."""

from typing import Optional, Tuple

from ...exceptions import TokenExpiredError, TokenInvalidError
from ...models import AccountType, TokenInfo
from ...permissions import permissions_from_json
from ...tokens import create_ws_ticket, verify_token_hash
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
