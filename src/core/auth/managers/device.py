from typing import List

from ..models import Device


class DeviceMixin:
    def get_devices(self, user_id: int) -> List[Device]:
        rows = self._db.fetch_all(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT * FROM auth_devices WHERE user_id = ?", (user_id,)
        )
        return [
            Device(
                id=r["id"],
                user_id=r["user_id"],
                fingerprint=r["fingerprint"],
                name=r["name"],
                device_type=r["device_type"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
            )
            for r in rows
        ]

    def rename_device(self, user_id: int, device_id: int, name: str) -> bool:
        cursor = self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_devices SET name = ? WHERE id = ? AND user_id = ?",
            (name, device_id, user_id),
        )
        return cursor.rowcount > 0

    def revoke_device(self, user_id: int, device_id: int) -> bool:
        cursor = self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "DELETE FROM auth_devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        )
        if cursor.rowcount == 0:
            return False
        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?", (device_id,)
        )
        return True
