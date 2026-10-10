"""
Media collector for media_files, user_avatars, auth_api_access_tokens tables.

Collects media file metadata (not blobs), avatar metadata (not blob data),
and API tokens (without secrets).
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class MediaCollector(BaseCollector):
    """Collects media files, avatars, and API token metadata."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect media files, avatars, and API tokens."""
        return {
            "media_files": self._collect_media_files(user_id),
            "avatars": self._collect_avatars(user_id),
            "api_tokens": self._collect_api_tokens(user_id),
        }

    def _collect_media_files(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect media_files metadata.

        Real columns are filename/original_filename/content_type/size/
        media_type/checksum/uploaded_by/uploaded_at/... (NOT file_name/
        file_type/file_size). Internal storage paths are excluded from
        the export.
        """
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, filename, original_filename, content_type,
                       size, media_type, checksum, uploaded_at,
                       scan_status
                FROM media_files
                WHERE uploaded_by = ? AND deleted = 0
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            self.record_error(f"Failed to collect media files: {e}")
            return []

    def _collect_avatars(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect user_avatars metadata.

        user_avatars stores the avatar as an ``avatar_data`` BLOB with
        content_type/width/height/size/checksum/uploaded_at columns -
        there is NO avatar_url column anywhere. The BLOB itself is not
        exported; only its metadata is included.
        """
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, content_type, width, height, size, checksum,
                       animated, uploaded_at
                FROM user_avatars WHERE user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            self.record_error(f"Failed to collect avatars: {e}")
            return []

    def _collect_api_tokens(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect auth_api_access_tokens without secrets."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, name, description, created_at, first_used_at,
                       last_used_at, expires_at, revoked, use_count_total,
                       scope_mode
                FROM auth_api_access_tokens WHERE created_by = ?
                """,
                (user_id,),
            )
            result = []
            for row in rows:
                r = dict(row)
                if "token_encrypted" in r:
                    del r["token_encrypted"]
                result.append(r)
            return result
        except Exception as e:
            self.record_error(f"Failed to collect API tokens: {e}")
            return []
