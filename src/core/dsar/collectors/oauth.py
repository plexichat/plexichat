"""
OAuth collector for auth_external_accounts table.

Collects external OAuth account linkages with redacted external IDs.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class OAuthCollector(BaseCollector):
    """Collects OAuth external account data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect external accounts."""
        return {"external_accounts": self._collect_accounts(user_id)}

    def _collect_accounts(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect auth_external_accounts with redacted external_id_encrypted."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, provider, external_id_encrypted, email_index, created_at, last_login_at
                FROM auth_external_accounts WHERE user_id = ?
                """,
                (user_id,),
            )
            result = []
            for row in rows:
                r = dict(row)
                if r.get("external_id_encrypted"):
                    r["external_id_encrypted"] = "(encrypted)"
                result.append(r)
            return result
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect OAuth accounts for user {user_id}: {e}")
            return []
