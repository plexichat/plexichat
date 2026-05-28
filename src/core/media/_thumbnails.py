# pyright: reportAttributeAccessIssue=false
"""
Thumbnail generation (sync + background) mixed into MediaManager.
"""

import logging
from typing import Dict

from .models import MediaType
from .exceptions import MediaError
from ._config import DEFAULT_THUMBNAIL_SIZES
from src.core import ratelimit

logger = logging.getLogger(__name__)


class _ThumbnailsMixin:
    """Thumbnail-related methods mixed into MediaManager."""

    # ── sync generation (legacy paths) ─────────────────────────────────────────

    def _generate_thumbnails(self, file_id: int, image_data: bytes) -> Dict[int, str]:
        if not self._image_processor:
            return {}
        sizes = self._config.get("thumbnail_sizes", DEFAULT_THUMBNAIL_SIZES)
        thumbnails: Dict[int, str] = {}
        try:
            results = self._image_processor.create_thumbnails(image_data, sizes)

            def _store_thumb(size, data_tuple):
                thumb_data, width, height = data_tuple
                thumb_path = f"thumbnails/{file_id}/{size}.jpg"
                self._storage.store(thumb_data, thumb_path, "image/jpeg")
                thumb_id = self._generate_id()
                now = self._get_timestamp()
                self._db.execute(
                    """INSERT INTO media_thumbnails
                       (id, media_file_id, size, width, height, storage_path, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (thumb_id, file_id, size, width, height, thumb_path, now),
                )
                return size, self._storage.get_url(thumb_path)

            futures = [
                self._executor.submit(_store_thumb, size, data)
                for size, data in results.items()
            ]
            for future in futures:
                try:
                    size, url = future.result()
                    thumbnails[size] = url
                except Exception as fe:
                    logger.warning(f"Failed to store thumbnail size: {fe}")
        except Exception as e:
            logger.warning(f"Failed to generate thumbnails: {e}")
        return thumbnails

    # ── should-skip check ──────────────────────────────────────────────────────

    def _should_skip_thumbnails(self, image_data: bytes) -> bool:
        """Skip if image is already ≤ smallest thumbnail size."""
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(image_data))
            smallest = min(self._config.get("thumbnail_sizes", DEFAULT_THUMBNAIL_SIZES))
            return img.width <= smallest and img.height <= smallest
        except Exception:
            return False

    # ── fire-and-forget background thumbnails ──────────────────────────────────

    def _generate_thumbnails_background(self, file_id: int, image_data: bytes):
        if not self._image_processor:
            return
        if self._should_skip_thumbnails(image_data):
            logger.debug(
                f"Skipping thumbnails for file {file_id} (image ≤ min thumbnail size)"
            )
            return
        sizes = self._config.get("thumbnail_sizes", DEFAULT_THUMBNAIL_SIZES)
        processor = self._image_processor
        self._executor.submit(
            self._do_generate_thumbnails, file_id, image_data, sizes, processor
        )

    def _do_generate_thumbnails(
        self, file_id: int, image_data: bytes, sizes: list, processor
    ):
        """Actual thumbnail work (runs in threadpool, fire-and-forget)."""
        try:
            results = processor.create_thumbnails(image_data, sizes)
            for size, (thumb_data, width, height) in results.items():
                try:
                    thumb_path = f"thumbnails/{file_id}/{size}.jpg"
                    self._storage.store(thumb_data, thumb_path, "image/jpeg")
                    thumb_id = self._generate_id()
                    now = self._get_timestamp()
                    self._db.execute(
                        """INSERT INTO media_thumbnails
                           (id, media_file_id, size, width, height, storage_path, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (thumb_id, file_id, size, width, height, thumb_path, now),
                    )
                except Exception as e:
                    logger.warning(
                        f"Background thumbnail store failed for size {size}: {e}"
                    )
        except Exception as e:
            logger.warning(
                f"Background thumbnail generation failed for file {file_id}: {e}"
            )

    # ── public thumbnail access ────────────────────────────────────────────────

    def get_thumbnails(self, file_id: int) -> Dict[int, str]:
        rows = self._db.fetch_all(
            "SELECT * FROM media_thumbnails WHERE media_file_id = ?", (file_id,)
        )
        return {row["size"]: self._storage.get_url(row["storage_path"]) for row in rows}

    def create_thumbnail(
        self,
        file_id: int,
        size: int,
        user_id: int | None = None,
    ) -> str | None:
        if not self._image_processor:
            return None
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.IMAGE:
            return None

        # Rate limit check
        if user_id is not None:
            rl_result = ratelimit.check_rate_limit(
                user_id=user_id, route="THUMBNAIL_GEN"
            )
            if not rl_result.allowed:
                raise MediaError(
                    f"Thumbnail generation rate limit exceeded. "
                    f"Please try again in {int(rl_result.retry_after or 1)}s"
                )

        existing = self._db.fetch_one(
            "SELECT * FROM media_thumbnails WHERE media_file_id = ? AND size = ?",
            (file_id, size),
        )
        if existing:
            return self._storage.get_url(existing["storage_path"])

        try:
            image_data = self._storage.retrieve(file.storage_path)
            thumb_data, width, height = self._image_processor.create_thumbnail(
                image_data, size
            )
            thumb_path = f"thumbnails/{file_id}/{size}.jpg"
            self._storage.store(thumb_data, thumb_path, "image/jpeg")
            thumb_id = self._generate_id()
            now = self._get_timestamp()
            self._db.execute(
                """INSERT INTO media_thumbnails
                   (id, media_file_id, size, width, height, storage_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (thumb_id, file_id, size, width, height, thumb_path, now),
            )
            return self._storage.get_url(thumb_path)
        except Exception as e:
            logger.warning(f"Failed to create thumbnail: {e}")
            return None
