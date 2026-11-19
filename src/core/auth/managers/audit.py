from typing import List

from src.core.security.hardening import bounded_int

from ..models import AuditEntry, AuditEventType


from .protocol import AuthManagerProtocol

# These audit reads are public core APIs with no HTTP route in front of them
# (see ``src.core.auth.get_login_history`` / ``get_security_events``), so every
# caller is a direct caller and the route's page constraints never apply. The
# ``limit`` is bound straight into ``LIMIT ?``; SQLite reads ``LIMIT -1`` as
# *unlimited*, so an unclamped negative value dumped the user's entire audit
# history (decrypted IP addresses, device ids). Enforce the page bound here at
# the shared manager boundary; non-integer / non-finite values are rejected.
_MAX_AUDIT_PAGE = 100


class AuditMixin(AuthManagerProtocol):
    def get_login_history(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        limit = bounded_int(limit, "limit", minimum=0, maximum=_MAX_AUDIT_PAGE)
        rows = self._db.fetch_all(
            "SELECT * FROM auth_audit_log WHERE user_id = ? AND event_type IN (?, ?, ?) ORDER BY timestamp DESC LIMIT ?",
            (
                user_id,
                AuditEventType.LOGIN_SUCCESS.value,
                AuditEventType.LOGIN_FAILED.value,
                AuditEventType.LOGOUT.value,
                limit,
            ),
        )
        return [
            AuditEntry(
                id=r["id"],
                user_id=r["user_id"],
                event_type=AuditEventType(r["event_type"]),
                ip_address=self.crypto.decrypt_data(
                    r["ip_encrypted"], context=str(r["id"])
                )
                if r.get("ip_encrypted")
                else None,
                device_id=r["device_id"],
                timestamp=r["timestamp"],
                details=None,
                success=bool(r["success"]),
            )
            for r in rows
        ]

    def get_security_events(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        limit = bounded_int(limit, "limit", minimum=0, maximum=_MAX_AUDIT_PAGE)
        rows = self._db.fetch_all(
            "SELECT * FROM auth_audit_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        )
        return [
            AuditEntry(
                id=r["id"],
                user_id=r["user_id"],
                event_type=AuditEventType(r["event_type"]),
                ip_address=self.crypto.decrypt_data(
                    r["ip_encrypted"], context=str(r["id"])
                )
                if r.get("ip_encrypted")
                else None,
                device_id=r["device_id"],
                timestamp=r["timestamp"],
                details=None,
                success=bool(r["success"]),
            )
            for r in rows
        ]
