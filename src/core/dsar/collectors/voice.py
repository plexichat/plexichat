"""
Voice collector for voice_states, voice_calls, and artifacts tables.

Collects voice state data, voice calls (initiated or consented to), and linked
artifacts (voice_call and transcript types) authored by the user.
Transcript text is included inline for DSAR portability.
"""

import json
from typing import Any, Dict, List

from ..base import BaseCollector


class VoiceCollector(BaseCollector):
    """Collects voice state, call, and artifact data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect all voice-related data."""
        return {
            "voice_states": self._collect_voice_states(user_id),
            "voice_calls": self._collect_voice_calls(user_id),
            "voice_call_artifacts": self._collect_call_artifacts(user_id),
            "transcripts": self._collect_transcripts(user_id),
        }

    def _collect_voice_states(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect voice_states."""
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM voice_states WHERE user_id = ?", (user_id,)
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect voice states for user {user_id}: {e}")
            return []

    def _collect_voice_calls(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect voice_calls where user is initiator or consented participant."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT * FROM voice_calls
                WHERE initiator_id = ?
                   OR (consented_participants IS NOT NULL
                       AND EXISTS (
                           SELECT 1 FROM json_each(consented_participants)
                           WHERE value = ?
                       ))
                """,
                (user_id, user_id),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect voice calls for user {user_id}: {e}")
            return []

    def _collect_call_artifacts(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect voice_call type artifacts."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT * FROM artifacts
                WHERE author_id = ? AND artifact_type = 'voice_call'
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(
                f"Failed to collect voice call artifacts for user {user_id}: {e}"
            )
            return []

    def _collect_transcripts(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect transcript type artifacts with flattened text."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT * FROM artifacts
                WHERE author_id = ? AND artifact_type = 'transcript'
                """,
                (user_id,),
            )
            result = []
            for row in rows:
                r = dict(row)
                payload = r.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (ValueError, TypeError):
                        payload = {}
                if isinstance(payload, dict):
                    r["transcript_text"] = payload.get("text", "")
                result.append(r)
            return result
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect transcripts for user {user_id}: {e}")
            return []
