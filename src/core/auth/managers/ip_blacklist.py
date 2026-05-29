from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.database import cached, invalidate_pattern


class IpBlacklistMixin:
    def block_ip(
        self,
        ip_address: str,
        reason: Optional[str] = None,
        blocked_by: Optional[int] = None,
        duration_hours: Optional[int] = None,
    ) -> bool:
        invalidate_pattern("ip_blocked:*")

        now = self._get_timestamp()  # pyright: ignore[reportAttributeAccessIssue]
        expires_at = now + (duration_hours * 3600 * 1000) if duration_hours else None

        ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")  # pyright: ignore[reportAttributeAccessIssue]
        ip_encrypted = self.crypto.encrypt_data(ip_address, context="ip_blacklist")  # pyright: ignore[reportAttributeAccessIssue]

        self._db.upsert(  # pyright: ignore[reportAttributeAccessIssue]
            "auth_ip_blacklist",
            [
                "ip_index",
                "ip_encrypted",
                "reason",
                "blocked_at",
                "blocked_by",
                "expires_at",
            ],
            (ip_index, ip_encrypted, reason, now, blocked_by, expires_at),
            conflict_columns=["ip_index"],
        )
        logger.info(f"IP blocked by {blocked_by}: {reason}")
        return True

    def unblock_ip(self, ip_address: str) -> bool:
        invalidate_pattern("ip_blocked:*")

        ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")  # pyright: ignore[reportAttributeAccessIssue]
        legacy_index = self.crypto.legacy_fast_blind_index(ip_address, "ip_address")  # pyright: ignore[reportAttributeAccessIssue]

        self._db.execute(  # pyright: ignore[reportAttributeAccessIssue]
            "DELETE FROM auth_ip_blacklist WHERE ip_index = ? OR (ip_index = ? AND ? != '')",
            (ip_index, legacy_index, legacy_index),
        )
        logger.info("IP unblocked")
        return True

    @cached(ttl=300, prefix="ip_blocked")
    def is_ip_blocked(self, ip_address: str) -> bool:
        try:
            ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")  # pyright: ignore[reportAttributeAccessIssue]
            legacy_index = self.crypto.legacy_fast_blind_index(ip_address, "ip_address")  # pyright: ignore[reportAttributeAccessIssue]

            row = self._db.fetch_one(  # pyright: ignore[reportAttributeAccessIssue]
                "SELECT expires_at FROM auth_ip_blacklist WHERE ip_index = ? OR (ip_index = ? AND ? != '')",
                (ip_index, legacy_index, legacy_index),
            )
            if not row:
                return False

            expires_at = row["expires_at"]
            if expires_at and expires_at < self._get_timestamp():  # pyright: ignore[reportAttributeAccessIssue]
                self.unblock_ip(ip_address)
                return False

            return True
        except Exception:
            return False

    def get_blocked_ips(self) -> List[Dict[str, Any]]:
        rows = self._db.fetch_all(  # pyright: ignore[reportAttributeAccessIssue]
            "SELECT * FROM auth_ip_blacklist ORDER BY blocked_at DESC"
        )
        for r in rows:
            try:
                r["ip_address"] = self.crypto.decrypt_data(  # pyright: ignore[reportAttributeAccessIssue]
                    r["ip_encrypted"], context="ip_blacklist"
                )
            except Exception:
                r["ip_address"] = "UNKNOWN"
        return rows
