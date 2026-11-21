import time
from typing import Dict, List, Optional

import utils.logger as logger
from src.core.database import invalidate_pattern

from ..gateway_revocation import close_session_gateway_connections
from ..models import AuditEventType, Device


from .protocol import AuthManagerProtocol


# ``is_trusted_device`` sits on the login hot path (TOTP enabled + a device
# fingerprint). Its schema guard used to introspect ``auth_devices`` (PRAGMA
# table_info / information_schema) on *every* probe, even though the trust
# columns are added once by migration and are permanent (ADD COLUMN). Login
# writes clear the DB's short-lived result cache, so that introspection really
# re-executed per request. Memoize the "columns present" verdict with a
# bounded, short-TTL cache keyed by the live db object (the strong reference
# prevents id() reuse). The TTL bounds staleness across workers, the cap keeps
# the cache tiny, and an explicit hook lets migrations/tests force a refresh.
# We only cache after ensure_trusted_devices_columns() returns normally; that
# helper adds any missing columns, so a cached True is safe for the table's
# lifetime. Trust state itself is never cached - only the schema fact.
_TRUSTED_COLS_TTL_SECONDS = 30.0
_TRUSTED_COLS_MAX_ENTRIES = 16
_trusted_columns_verdicts: Dict[int, tuple] = {}  # id(db) -> (db, expires_at_monotonic)


def invalidate_trusted_device_schema_cache() -> None:
    """Force the next trusted-device probe to re-introspect the schema.

    Call after a migration that (re)creates ``auth_devices`` so the memo cannot
    outlive the schema it describes.
    """
    _trusted_columns_verdicts.clear()


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
        # Capture the sessions this device owns before revoking them so their
        # sockets can be closed individually. A blanket user-wide close would
        # also kill the user's other, still-valid devices.
        owned = self._db.fetch_all(
            "SELECT id FROM auth_sessions WHERE device_id = ? AND revoked = 0",
            (device_id,),
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?", (device_id,)
        )
        # Invalidate AFTER the write, exactly as logout/revoke_session/
        # disable_2fa do: ``verify_token`` is cached for 10s, so a session
        # this call just revoked would otherwise keep authenticating from a
        # warm cache entry (and be re-cached) for the length of the TTL.
        invalidate_pattern("token_verify:*")
        self._log_audit(AuditEventType.DEVICE_REVOKED, user_id, True)
        for row in owned:
            close_session_gateway_connections(
                user_id, int(row["id"]), "Session revoked"
            )
        return True

    # ------------------------------------------------------------------
    # Trusted devices (2FA skip)
    # ------------------------------------------------------------------

    def _ensure_trusted_device_columns(self) -> None:
        """Lazy guard so databases created before the trust columns existed
        are covered without paying the DDL cost on every request.

        The verdict is memoized per db object (bounded + short TTL) because the
        trust columns are permanent once present; see the module-level cache
        note. This keeps a trusted-device login probe at a single SELECT.
        """
        db = self._db
        key = id(db)
        now = time.monotonic()
        cached = _trusted_columns_verdicts.get(key)
        if cached is not None and cached[0] is db and cached[1] > now:
            return
        from ..schema import ensure_trusted_devices_columns

        ensure_trusted_devices_columns(db)
        if len(_trusted_columns_verdicts) >= _TRUSTED_COLS_MAX_ENTRIES:
            _trusted_columns_verdicts.clear()
        _trusted_columns_verdicts[key] = (db, now + _TRUSTED_COLS_TTL_SECONDS)

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
        # Sessions on this fingerprint are about to be revoked; record them so
        # only their sockets are closed, not sessions on other devices.
        owned = self._db.fetch_all(
            "SELECT s.id AS id FROM auth_sessions s JOIN auth_devices d "
            "ON s.device_id = d.id WHERE d.user_id = ? AND d.fingerprint = ? "
            "AND s.revoked = 0",
            (user_id, fingerprint),
        )
        self._db.execute(
            "UPDATE auth_sessions SET revoked = 1 WHERE device_id IN "
            "(SELECT id FROM auth_devices WHERE user_id = ? AND fingerprint = ?)",
            (user_id, fingerprint),
        )
        invalidate_pattern("token_verify:*")
        self._log_audit(AuditEventType.DEVICE_UNTRUSTED, user_id, True)
        for row in owned:
            close_session_gateway_connections(
                user_id, int(row["id"]), "Session revoked"
            )
        return True
