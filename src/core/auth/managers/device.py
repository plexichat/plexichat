from typing import List

import utils.logger as logger

from ..models import Device


from .protocol import AuthManagerProtocol


class DeviceMixin(AuthManagerProtocol):
    def _decrypt_device_field(
        self, row, field: str, encrypted_field: str
    ) -> str | None:
        if row.get(encrypted_field):
            try:
                return self.crypto.decrypt_data(
                    row[encrypted_field], context=f"device:{row['id']}"
                )
            except Exception as e:
                logger.warning(f"Failed to decrypt {field} for device {row['id']}: {e}")
        return row.get(field)

    def get_devices(self, user_id: int) -> List[Device]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_devices WHERE user_id = ?", (user_id,)
        )
        return [
            Device(
                id=r["id"],
                user_id=r["user_id"],
                fingerprint=self._decrypt_device_field(
                    r, "fingerprint", "fingerprint_encrypted"
                )
                or "",
                name=self._decrypt_device_field(r, "name", "name_encrypted"),
                device_type=self._decrypt_device_field(
                    r, "device_type", "device_type_encrypted"
                ),
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
            )
            for r in rows
        ]

    def rename_device(self, user_id: int, device_id: int, name: str) -> bool:
        name_encrypted = None
        try:
            name_encrypted = self.crypto.encrypt_data(
                name, context=f"device:{device_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to encrypt device name on rename: {e}")
        if name_encrypted is not None:
            cursor = self._db.execute(
                "UPDATE auth_devices SET name = ?, name_encrypted = ? WHERE id = ? AND user_id = ?",
                (name, name_encrypted, device_id, user_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE auth_devices SET name = ? WHERE id = ? AND user_id = ?",
                (name, device_id, user_id),
            )
        return cursor.rowcount > 0

    def revoke_device(self, user_id: int, device_id: int) -> bool:
        cursor = self._db.execute(
            "DELETE FROM auth_devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        )
        if cursor.rowcount == 0:
            return False
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?", (device_id,)
        )
        return True
