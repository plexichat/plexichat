from typing import Optional, List

from src.core.base import SnowflakeID
from ..models import Violation, ViolationSeverity, UserReputation
from .converters import row_to_violation, row_to_reputation


from .protocol import AutoModProtocol


class ReputationMixin(AutoModProtocol):
    def get_violations(
        self,
        server_id: SnowflakeID,
        user_id: Optional[SnowflakeID] = None,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
    ) -> List[Violation]:
        query = "SELECT * FROM automod_violations WHERE server_id = ?"
        params = [server_id]

        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        if before_id is not None:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(limit, 100))

        rows = self._db.fetch_all(query, tuple(params))
        return [row_to_violation(row) for row in rows]

    def get_user_reputation(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> UserReputation:
        row = self._db.fetch_one(
            "SELECT * FROM automod_reputation WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )

        if not row:
            now = self._get_timestamp()
            return UserReputation(
                user_id=user_id,
                server_id=server_id,
                score=100.0,
                violation_count=0,
                last_violation_at=None,
                last_decay_at=now,
                created_at=now,
                updated_at=now,
            )

        return row_to_reputation(row)

    def _update_reputation(
        self, user_id: SnowflakeID, server_id: SnowflakeID, severity: ViolationSeverity
    ):
        penalty_map = {
            ViolationSeverity.LOW: 5,
            ViolationSeverity.MEDIUM: 10,
            ViolationSeverity.HIGH: 20,
            ViolationSeverity.CRITICAL: 40,
        }
        penalty = penalty_map.get(severity, 10)

        now = self._get_timestamp()

        existing = self._db.fetch_one(
            "SELECT * FROM automod_reputation WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )

        if existing:
            new_score = max(0, existing["score"] - penalty)
            self._db.execute(
                """UPDATE automod_reputation 
                   SET score = ?, violation_count = violation_count + 1, 
                       last_violation_at = ?, updated_at = ?
                   WHERE user_id = ? AND server_id = ?""",
                (new_score, now, now, user_id, server_id),
            )
        else:
            self._db.execute(
                """INSERT INTO automod_reputation 
                   (user_id, server_id, score, violation_count, last_violation_at, 
                    last_decay_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, server_id, 100 - penalty, 1, now, now, now, now),
            )

    def decay_reputation(self, server_id: Optional[SnowflakeID] = None) -> int:
        decay_rate = self._config.get("reputation_decay_rate", 1.0)
        decay_interval = self._config.get("reputation_decay_interval", 86400) * 1000

        now = self._get_timestamp()
        cutoff = now - decay_interval

        query = """
            UPDATE automod_reputation 
            SET score = MIN(100, score + ?), last_decay_at = ?, updated_at = ?
            WHERE last_decay_at < ? AND score < 100
        """
        params = [decay_rate, now, now, cutoff]

        if server_id is not None:
            query = query.replace("WHERE", "WHERE server_id = ? AND")
            params.insert(0, server_id)

        self._db.execute(query, tuple(params))

        result = self._db.fetch_one("SELECT changes() as count")
        count = result["count"] if result else 0
        return count
