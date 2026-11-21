from typing import Dict, List, Optional

import utils.logger as logger
from src.core.database import invalidate_pattern

from ..models import AuditEventType, Device


from .protocol import AuthManagerProtocol


# Keep batched lookups inside SQLite's classic bind-parameter ceiling and well
# within PostgreSQL's. The admin device overview walks up to MAX_OVERVIEW_USERS
# ids; one bounded IN chunk per batch replaces its former two queries *per
# user* without changing the generated SQL shape as the user set grows.
_BULK_IN_CHUNK = 500


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

    def _device_from_row(self, r) -> Device:
        """Build one Device from a row; shared by the single and bulk paths."""
        return Device(
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
            trusted=bool(r.get("trusted", 0)),
            trusted_at=r.get("trusted_at"),
            first_seen_at=r["first_seen_at"],
            last_seen_at=r["last_seen_at"],
        )

    def get_devices(self, user_id: int) -> List[Device]:
        rows = self._db.fetch_all(
            "SELECT * FROM auth_devices WHERE user_id = ?", (user_id,)
        )
        return [self._device_from_row(r) for r in rows]

    def get_devices_bulk(self, user_ids) -> Dict[int, List[Device]]:
        """Fetch devices for many users in bounded IN chunks.

        Every row is grouped by the ``user_id`` it carries *and* checked
        against the explicit requested set, so a row belonging to a user
        outside that set is dropped instead of leaking into another user's
        group. Live DB reads (no process-local cache) keep this correct across
        workers: a trust/revoke on another node is visible on the next call.
        """
        ids = sorted({int(uid) for uid in user_ids})
        by_user: Dict[int, List[Device]] = {uid: [] for uid in ids}
        if not ids:
            return by_user
        for start in range(0, len(ids), _BULK_IN_CHUNK):
            chunk = ids[start : start + _BULK_IN_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                f"SELECT * FROM auth_devices WHERE user_id IN ({placeholders})",
                tuple(chunk),
            )
            for r in rows:
                uid = int(r["user_id"])
                if uid not in by_user:
                    continue  # fail closed: never group an unrequested row
                by_user[uid].append(self._device_from_row(r))
        return by_user

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
        # Invalidate AFTER the write, exactly as logout/revoke_session/
        # disable_2fa do: ``verify_token`` is cached for 10s, so a session
        # this call just revoked would otherwise keep authenticating from a
        # warm cache entry (and be re-cached) for the length of the TTL.
        invalidate_pattern("token_verify:*")
        self._log_audit(AuditEventType.DEVICE_REVOKED, user_id, True)
        return True

    # ------------------------------------------------------------------
    # Trusted devices (2FA skip)
    # ------------------------------------------------------------------

    def _ensure_trusted_device_columns(self) -> None:
        """Lazy guard so databases created before the trust columns existed
        are covered without paying the DDL cost on every request."""
        from ..schema import ensure_trusted_devices_columns

        ensure_trusted_devices_columns(self._db)

    def is_trusted_device(self, user_id: int, fingerprint: Optional[str]) -> bool:
        """True when this (user, fingerprint) pair is marked trusted.

        SECURITY: a missing fingerprint can never be trusted — an attacker
        who strips the fingerprint header must not inherit trust.
        """
        if not fingerprint:
            return False
        self._ensure_trusted_device_columns()
        row = self._db.fetch_one(
            "SELECT trusted FROM auth_devices WHERE user_id = ? AND fingerprint = ?",
            (user_id, fingerprint),
        )
        return bool(row and row.get("trusted"))

    def trust_device(self, user_id: int, fingerprint: Optional[str]) -> bool:
        """Mark a known device as trusted (2FA skip). Idempotent."""
        if not fingerprint:
            return False
        self._ensure_trusted_device_columns()
        now = self._get_timestamp()
        # Only mark trust on a device we have seen before: trusting an
        # unknown fingerprint would let an attacker pre-trust a device
        # they have never authenticated from.
        cursor = self._db.execute(
            "UPDATE auth_devices SET trusted = 1, trusted_at = ? WHERE user_id = ? AND fingerprint = ?",
            (now, user_id, fingerprint),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            return False
        self._log_audit(AuditEventType.DEVICE_TRUSTED, user_id, True)
        return True

    def untrust_device(self, user_id: int, fingerprint: Optional[str]) -> bool:
        """Remove trust from a device. Idempotent."""
        if not fingerprint:
            return False
        self._ensure_trusted_device_columns()
        cursor = self._db.execute(
            "UPDATE auth_devices SET trusted = 0, trusted_at = NULL WHERE user_id = ? AND fingerprint = ?",
            (user_id, fingerprint),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            return False
        self._log_audit(AuditEventType.DEVICE_UNTRUSTED, user_id, True)
        return True
