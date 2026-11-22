from typing import List

from src.core.base import SnowflakeID

from ..exceptions import ServerNotFoundError
from .protocol import ServerProtocol
from .converters import _row_to_invite


class InviteOpsMixin(ServerProtocol):
    """Mixin for invite operations."""

    def get_invites(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List:
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "invites.manage")

        rows = self._db.fetch_all(
            "SELECT * FROM srv_invites WHERE server_id = ? AND revoked = 0 ORDER BY created_at DESC",
            (server_id,),
        )

        return [_row_to_invite(row) for row in rows]

    def get_server_invites(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List:
        return self.get_invites(user_id, server_id)

    # Keep each IN (...) clause below the SQLite/PostgreSQL bound-parameter
    # ceiling; the automod resolver caps its candidate list, but chunking keeps
    # this helper safe for any caller.
    _INVITE_CODE_LOOKUP_CHUNK = 500

    def get_existing_invite_codes(self, codes) -> set:
        """Return the subset of *codes* that map to a live, unrevoked invite.

        Set-based existence check used by the automod invite resolver so one
        message with several candidate codes costs one query (chunked above
        the DB parameter limit) instead of one ``get_invite`` query per code.
        The predicate matches ``get_invite`` exactly (``revoked = 0`` only), so
        visibility/authorization is unchanged. Invite codes are globally unique
        and the result carries no viewer state, so it is safe to share.
        """
        normalized = list(dict.fromkeys(str(code) for code in codes))
        found = set()
        for start in range(0, len(normalized), self._INVITE_CODE_LOOKUP_CHUNK):
            chunk = normalized[start : start + self._INVITE_CODE_LOOKUP_CHUNK]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                "SELECT code FROM srv_invites "
                f"WHERE revoked = 0 AND code IN ({placeholders})",
                tuple(chunk),
            )
            for row in rows or []:
                code = row["code"]
                if isinstance(code, str):
                    found.add(code)
        return found
