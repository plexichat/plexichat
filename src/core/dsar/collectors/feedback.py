"""
Feedback collector for feedback table.

Collects user feedback submissions.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class FeedbackCollector(BaseCollector):
    """Collects user feedback."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect feedback submissions."""
        return {"feedback": self._collect_feedback(user_id)}

    def _collect_feedback(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect feedback rows."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM feedback WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect feedback for user {user_id}: {e}")
            return []
