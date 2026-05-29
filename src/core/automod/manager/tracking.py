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

        row = self._db.fetch_one(
            """SELECT id, count FROM automod_rate_tracking
               WHERE server_id = ? AND user_id = ? AND rule_type = ? AND window_start = ?""",
            (server_id, user_id, rule_type, window_start),
        )

        if row:
            new_count = int(row["count"]) + 1
            self._db.execute(
                "UPDATE automod_rate_tracking SET count = ? WHERE id = ?",
                (new_count, row["id"]),
            )
            return new_count, window_start

        rate_id = self._generate_id()
        self._db.execute(
            """INSERT INTO automod_rate_tracking
               (id, server_id, user_id, rule_type, window_start, count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rate_id, server_id, user_id, rule_type, window_start, 1),
        )
        return 1, window_start
