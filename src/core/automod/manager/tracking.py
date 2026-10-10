from src.core.base import SnowflakeID


from .protocol import AutoModProtocol


class TrackingMixin(AutoModProtocol):
    def _get_rate_window_ms(self) -> int:
        window_seconds = int(self._config.get("rate_limit_window", 60))
        if window_seconds <= 0:
            return 60000
        return window_seconds * 1000

    def _increment_rate_tracking(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        rule_type: str,
        now: int,
    ) -> tuple:
        window_ms = self._get_rate_window_ms()
        window_start = now - (now % window_ms)

        # E4: the previous SELECT-then-UPDATE/INSERT was a read-modify-write
        # race — two concurrent violations could both miss the SELECT, then one
        # INSERT dies on the UNIQUE constraint and (via the fail-open fast
        # check) lets the violating message through. Use a conditional
        # UPDATE-first upsert instead: concurrent losers of the INSERT race
        # re-apply as UPDATEs, so every violation is counted exactly once.
        cursor = self._db.execute(
            "UPDATE automod_rate_tracking SET count = count + 1 "
            "WHERE server_id = ? AND user_id = ? AND rule_type = ? "
            "AND window_start = ?",
            (server_id, user_id, rule_type, window_start),
        )
        if getattr(cursor, "rowcount", 0) > 0:
            row = self._db.fetch_one(
                """SELECT count FROM automod_rate_tracking
                   WHERE server_id = ? AND user_id = ? AND rule_type = ?
                   AND window_start = ?""",
                (server_id, user_id, rule_type, window_start),
            )
            return int(row["count"]) if row else 1, window_start

        rate_id = self._generate_id()
        try:
            self._db.execute(
                """INSERT INTO automod_rate_tracking
                   (id, server_id, user_id, rule_type, window_start, count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (rate_id, server_id, user_id, rule_type, window_start, 1),
            )
            return 1, window_start
        except Exception as e:
            # Lost the concurrent-insert race: another request created the row
            # between our UPDATE (no-op) and INSERT. Apply as an UPDATE instead
            # of surfacing an IntegrityError into the fail-open fast check.
            lowered = str(e).lower()
            if "unique" in lowered or "integrity" in lowered:
                self._db.execute(
                    "UPDATE automod_rate_tracking SET count = count + 1 "
                    "WHERE server_id = ? AND user_id = ? AND rule_type = ? "
                    "AND window_start = ?",
                    (server_id, user_id, rule_type, window_start),
                )
                row = self._db.fetch_one(
                    """SELECT count FROM automod_rate_tracking
                       WHERE server_id = ? AND user_id = ? AND rule_type = ?
                       AND window_start = ?""",
                    (server_id, user_id, rule_type, window_start),
                )
                return int(row["count"]) if row else 1, window_start
            raise
