# pyright: reportAttributeAccessIssue=false
"""
Background pHash dedup-work mixed into MediaManager.

Runs after the upload response is already sent to keep latency low.
"""

import logging

logger = logging.getLogger(__name__)


class _PhashMixin:
    """Background pHash computation + similarity check (fire-and-forget)."""

    def _do_background_phash_dedup(
        self,
        checksum: str,
        file_data: bytes,
        content_type: str,
        user_id: int,
        file_size: int,
    ):
        """Compute pHash for images and check against blocked/similar hashes.

        Requires file_data to already be in memory (no temp file access).
        Runs after upload response is already sent to the client.
        """
        if not self._dedup_manager:
            return
        try:
            phash_value = self._dedup_manager.compute_phash(file_data, content_type)
            if not phash_value:
                return

            # Update pHash on existing record or create new one
            self._dedup_manager.register_or_update_phash(
                hash_value=checksum,
                phash_value=phash_value,
                file_size=file_size,
                content_type=content_type,
                timestamp=self._get_timestamp(),
            )

            # Check pHash against blocked hashes (non-blocking — just logs)
            is_blocked, reason = self._dedup_manager.is_blocked(
                checksum, phash_value=phash_value
            )
            if is_blocked:
                logger.warning(
                    f"Background pHash check: file from user {user_id} "
                    f"matches blocked pHash: {reason}"
                )
        except Exception as e:
            logger.debug(f"Background pHash dedup failed: {e}")
