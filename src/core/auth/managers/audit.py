from typing import List

from ..models import AuditEntry, AuditEventType


class AuditMixin:
    def get_login_history(self, user_id: int, limit: int = 50) -> List[AuditEntry]:
        rows = self._db.fetch_all(  # pyright: ignore[reportAttributeAccessIssue]
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
                ip_address=self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
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
        rows = self._db.fetch_all(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT * FROM auth_audit_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        )
        return [
            AuditEntry(
                id=r["id"],
                user_id=r["user_id"],
                event_type=AuditEventType(r["event_type"]),
                ip_address=self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
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
