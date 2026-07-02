# pyright: reportAttributeAccessIssue=false
"""
Image/video processing methods mixed into MediaManager.
"""

import logging
from typing import Optional

from .models import MediaType, VideoMetadata
from .exceptions import MediaError, ImageProcessingError
from .processing import ImageProcessor, VideoProcessor

logger = logging.getLogger(__name__)


class _ProcessingMixin:
    """Image & video processing methods mixed into MediaManager."""

    # ── initialisation ─────────────────────────────────────────────────────────

    def _init_image_processor(self) -> Optional[ImageProcessor]:
        try:
            return ImageProcessor(
                quality=self._config.get("image_quality", 85),
                optimize=self._config.get("image_optimize", True),
            )
        except ImageProcessingError:
            logger.warning("Image processing unavailable - Pillow not installed")
            return None

    def _init_video_processor(self) -> VideoProcessor:
        processor = VideoProcessor(
            ffprobe_path=self._config.get("ffprobe_path"),
        )
        if not processor.is_available():
            logger.warning("Video processing unavailable - ffprobe not found")
        return processor

    # ── public API ─────────────────────────────────────────────────────────────

    def resize_image(
        self,
        file_id: int,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> bytes:
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.IMAGE:
            raise MediaError("File not found or not an image")
        if not self._image_processor:
            raise ImageProcessingError("Image processing not available", "resize")
        image_data = self._storage.retrieve(file.storage_path)
        resized, _, _ = self._image_processor.resize(image_data, width, height)
        return resized

    def convert_image(self, file_id: int, output_format: str) -> bytes:
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.IMAGE:
            raise MediaError("File not found or not an image")
        if not self._image_processor:
            raise ImageProcessingError("Image processing not available", "convert")
        image_data = self._storage.retrieve(file.storage_path)
        return self._image_processor.convert_format(image_data, output_format)

    def get_video_metadata(self, file_id: int) -> Optional[VideoMetadata]:
        file = self.get_file(file_id)
        if not file or file.media_type != MediaType.VIDEO:
            return None
        if not self._video_processor or not self._video_processor.is_available():
            return None
        try:
            video_data = self._storage.retrieve(file.storage_path)
            return self._video_processor.get_metadata_from_bytes(video_data)
        except Exception as e:
            logger.warning(f"Failed to get video metadata: {e}")
            return None
