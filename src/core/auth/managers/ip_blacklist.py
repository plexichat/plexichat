from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.database import cached, invalidate_pattern


from .protocol import AuthManagerProtocol


class IpBlacklistMixin(AuthManagerProtocol):
    def block_ip(
        self,
        ip_address: str,
        reason: Optional[str] = None,
        blocked_by: Optional[int] = None,
        duration_hours: Optional[int] = None,
    ) -> bool:
        now = self._get_timestamp()
        expires_at = now + (duration_hours * 3600 * 1000) if duration_hours else None

        ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
        ip_encrypted = self.crypto.encrypt_data(ip_address, context="ip_blacklist")

        self._db.upsert(
            "auth_ip_blacklist",
            [
                "ip_index",
                "ip_encrypted",
                "reason",
                "reason_encrypted",
                "blocked_at",
                "blocked_by",
                "expires_at",
            ],
            (
                ip_index,
                ip_encrypted,
                reason,
                self.crypto.encrypt_data(reason, context=f"ip_block:{ip_index}")
                if reason
                else None,
                now,
                blocked_by,
                expires_at,
            ),
            conflict_columns=["ip_index"],
        )
        # Invalidate AFTER the write: ``is_ip_blocked`` memoises each per-IP
        # verdict for 300s. A check landing between an early invalidation and
        # this upsert would re-cache the still-"not blocked" verdict, leaving a
        # just-blocked IP able to pass the gate for the rest of the TTL.
        invalidate_pattern("ip_blocked:*")
        logger.info(f"IP blocked by {blocked_by}: {reason}")
        return True

    def unblock_ip(self, ip_address: str) -> bool:
        ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
        legacy_index = self.crypto.legacy_fast_blind_index(ip_address, "ip_address")

        self._db.execute(
            "DELETE FROM auth_ip_blacklist WHERE ip_index = ? OR (ip_index = ? AND ? != '')",
            (ip_index, legacy_index, legacy_index),
        )
        # Invalidate after the write (see ``block_ip``): a check landing
        # between an early invalidation and this DELETE would re-cache the
        # stale "blocked" verdict and keep a legitimately unblocked IP locked
        # out for the rest of the TTL.
        invalidate_pattern("ip_blocked:*")
        logger.info("IP unblocked")
        return True

    @cached(ttl=300, prefix="ip_blocked")
    def is_ip_blocked(self, ip_address: str) -> bool:
        # Fail OPEN on DB/crypto/cache errors: a transient outage must not
        # turn into a site-wide lockout. Listed IPs are still blocked via
        # the normal DB path below; only the error path returns False.
        try:
            ip_index = self.crypto.fast_blind_index(ip_address, "ip_address")
            legacy_index = self.crypto.legacy_fast_blind_index(ip_address, "ip_address")

            row = self._db.fetch_one(
                "SELECT expires_at FROM auth_ip_blacklist WHERE ip_index = ? OR (ip_index = ? AND ? != '')",
                (ip_index, legacy_index, legacy_index),
            )
            if not row:
                return False

            expires_at = row["expires_at"]
            if expires_at and expires_at < self._get_timestamp():
                self.unblock_ip(ip_address)
                return False

            return True
        except Exception as e:
            logger.warning(
                f"is_ip_blocked: failed to check IP blacklist for {ip_address}: {e}"
            )
            return False

    def get_blocked_ips(
        self, limit: int = 200, offset: int = 0
    ) -> List[Dict[str, Any]]:
        # Bounded, deterministic page: the admin route passes validated
        # values; callers that omit them keep a sane default instead of an
        # unbounded decrypt+serialize pass over an attacker-growable table.
        limit = max(1, min(int(limit), 500))
        offset = max(0, min(int(offset), 1_000_000))
        rows = self._db.fetch_all(
            "SELECT * FROM auth_ip_blacklist ORDER BY blocked_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        for r in rows:
            try:
                r["ip_address"] = self.crypto.decrypt_data(
                    r["ip_encrypted"], context="ip_blacklist"
                )
            except Exception:
                r["ip_address"] = "UNKNOWN"
            if r.get("reason_encrypted"):
                try:
                    r["reason"] = self.crypto.decrypt_data(
                        r["reason_encrypted"],
                        context=f"ip_block:{r.get('ip_index', '')}",
                    )
                except Exception as exc:
                    # The block itself is still returned; without a reason the
                    # admin UI shows an empty note, which the log explains.
                    logger.debug(
                        f"ip block reason decryption failed for {r.get('ip_index')!r}: {exc}"
                    )
        return rows
