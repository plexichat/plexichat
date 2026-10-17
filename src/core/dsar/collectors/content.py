"""
Content collector for msg_pinned, react_reactions, msg_attachments tables.

Collects pinned messages, reactions, and attachment metadata.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class ContentCollector(BaseCollector):
    """Collects content interaction data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect pinned messages, reactions, and attachments."""
        return {
            "pinned_messages": self._collect_pinned(user_id),
            "reactions": self._collect_reactions(user_id),
            "attachments": self._collect_attachments(user_id),
        }

    def _collect_pinned(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_pinned.

        msg_pinned is keyed by ``pinned_by`` (there is NO user_id
        column); see src/core/messaging/schema.py.
        """
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_pinned WHERE pinned_by = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            self.record_error(f"Failed to collect pinned: {e}")
            return []

    def _collect_reactions(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect react_reactions."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM react_reactions WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            self.record_error(f"Failed to collect reactions: {e}")
            return []

    def _collect_attachments(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect msg_attachments metadata for the user's messages.

        msg_attachments has NO owner column; ownership is derived via
        the author of the parent message in msg_messages. Real columns
        are filename/content_type/size/url/... (NOT file_name/file_type/
        file_size).
        """
        try:
            rows = self._db.fetch_all(
                """
                SELECT a.id, a.message_id, a.filename, a.content_type,
                       a.size, a.url, a.checksum, a.created_at
                FROM msg_attachments a
                JOIN msg_messages m ON m.id = a.message_id
                WHERE m.author_id = ? AND a.deleted = 0
                """,
                (user_id,),
            )
            # Redact ciphertext fields per DSAR redaction policy.
            return self.serialize_rows(rows, redact_fields=["url_encrypted"])
        except Exception as e:
            self.record_error(f"Failed to collect attachments: {e}")
            return []
