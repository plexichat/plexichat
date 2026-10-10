"""
Applications collector for app_applications, app_installations, app_oauth_tokens tables.

Collects owned applications, installations, and OAuth tokens.
Redacts bot tokens and public keys from owned applications.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class ApplicationsCollector(BaseCollector):
    """Collects application data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect owned apps, installations, and OAuth tokens."""
        return {
            "owned_applications": self._collect_owned(user_id),
            "installations": self._collect_installations(user_id),
            "oauth_tokens": self._collect_tokens(user_id),
        }

    def _collect_owned(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect app_applications owned by user, redacting secrets."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM app_applications WHERE owner_id = ?", (user_id,)
            )
            result = []
            for row in rows:
                r = dict(row)
                for field in ("bot_token_encrypted", "public_key_encrypted"):
                    if field in r:
                        del r[field]
                result.append(r)
            return result
        except Exception as e:
            import utils.logger as logger

            logger.error(
                f"Failed to collect owned applications for user {user_id}: {e}"
            )
            return []

    def _collect_installations(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect app_installations."""
        try:
            # app_installations has no `user_id`; the granting user is recorded
            # as `installer_id`. The old predicate referenced a non-existent
            # column, so this collector always raised and returned [].
            rows = self._db.fetch_all(
                "SELECT * FROM app_installations WHERE installer_id = ?",
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect installations for user {user_id}: {e}")
            return []

    def _collect_tokens(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect app_oauth_tokens with limited fields."""
        try:
            # Projected onto the live columns. app_oauth_tokens has no `name`,
            # `description`, `first_used_at` or `use_count_total`, so the old
            # projection raised "no such column" and exported no tokens at all.
            rows = self._db.fetch_all(
                """
                SELECT id, application_id, user_id, scopes, expires_at,
                       created_at, revoked, last_used_at
                FROM app_oauth_tokens WHERE user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect OAuth tokens for user {user_id}: {e}")
            return []
