"""
Video metadata extraction using ffprobe.

Security features:
- Configurable timeout to prevent hanging on malformed files
- File size limits for metadata extraction
- Subprocess isolation
"""

import json
import subprocess
import shutil
from typing import Optional

import utils.config as config
import utils.logger as logger

from ..models import VideoMetadata
from ..exceptions import VideoProcessingError


# Default limits
DEFAULT_FFPROBE_TIMEOUT = 30  # seconds
DEFAULT_MAX_VIDEO_SIZE_FOR_METADATA = 500 * 1024 * 1024  # 500MB


def _get_video_config() -> dict:
    """Get video processing configuration."""
    try:
        media_config = config.get("media", {})
        return media_config.get("video_processing", {})
    except RuntimeError:
        return {}


class VideoProcessor:
    """Video metadata extraction using ffprobe with security limits."""

    SUPPORTED_FORMATS = {
        "video/mp4",
        "video/webm",
        "video/ogg",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-matroska",
        "video/x-flv",
        "video/3gpp",
        "video/3gpp2",
    }

    def __init__(
        self,
        ffprobe_path: Optional[str] = None,
        timeout: Optional[int] = None,
        max_size_for_metadata: Optional[int] = None,
    ):
        """
        Initialize video processor.
        
        Args:
            ffprobe_path: Path to ffprobe executable (auto-detected if None)
            timeout: Timeout in seconds for ffprobe (default: 30)
            max_size_for_metadata: Max file size to extract metadata from (default: 500MB)
        """
        self._ffprobe_path = ffprobe_path or self._find_ffprobe()

        # Load config with fallbacks
        video_config = _get_video_config()
        self._timeout = timeout or video_config.get("ffprobe_timeout", DEFAULT_FFPROBE_TIMEOUT)
        self._max_size = max_size_for_metadata or video_config.get(
            "max_size_for_metadata", DEFAULT_MAX_VIDEO_SIZE_FOR_METADATA
        )

        if not self._ffprobe_path:
            logger.warning("ffprobe not found - video metadata extraction disabled")

    def _find_ffprobe(self) -> Optional[str]:
        """Find ffprobe executable in PATH."""
        path = shutil.which("ffprobe")
        if path:
            logger.debug(f"Found ffprobe at {path}")
        return path

    def is_available(self) -> bool:
        """Check if ffprobe is available."""
        return self._ffprobe_path is not None

    def get_metadata(self, file_path: str) -> VideoMetadata:
        """
        Extract metadata from video file.
        
        Args:
            file_path: Path to video file
            
        Returns:
            VideoMetadata object
        """
        if not self._ffprobe_path:
            raise VideoProcessingError("ffprobe not available", "metadata")

        try:
            cmd = [
                self._ffprobe_path,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )

            if result.returncode != 0:
                raise VideoProcessingError(
                    f"ffprobe failed: {result.stderr}",
                    "metadata"
                )

            data = json.loads(result.stdout)

            return self._parse_ffprobe_output(data)
        except subprocess.TimeoutExpired:
            raise VideoProcessingError("ffprobe timed out", "metadata")
        except json.JSONDecodeError as e:
            raise VideoProcessingError(f"Failed to parse ffprobe output: {e}", "metadata")
        except Exception as e:
            logger.error(f"Failed to extract video metadata: {e}")
            raise VideoProcessingError(f"Failed to extract metadata: {e}", "metadata")

    def get_metadata_from_bytes(self, video_data: bytes) -> VideoMetadata:
        """
        Extract metadata from video bytes.
        
        Args:
            video_data: Raw video bytes
            
        Returns:
            VideoMetadata object
            
        Raises:
            VideoProcessingError: If file too large or ffprobe fails/times out
        """
        if not self._ffprobe_path:
            raise VideoProcessingError("ffprobe not available", "metadata")

        # Security: Check file size before processing
        if len(video_data) > self._max_size:
            logger.warning(
                f"Video too large for metadata extraction: {len(video_data)} > {self._max_size}"
            )
            raise VideoProcessingError(
                f"Video exceeds maximum size for metadata extraction ({self._max_size // (1024*1024)}MB)",
                "metadata"
            )

        try:
            cmd = [
                self._ffprobe_path,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                "-i", "pipe:0",
            ]

            result = subprocess.run(
                cmd,
                input=video_data,
                capture_output=True,
                timeout=self._timeout,
            )

            if result.returncode != 0:
                raise VideoProcessingError(
                    f"ffprobe failed: {result.stderr.decode()}",
                    "metadata"
                )

            data = json.loads(result.stdout.decode())

            return self._parse_ffprobe_output(data)
        except subprocess.TimeoutExpired:
            raise VideoProcessingError("ffprobe timed out", "metadata")
        except json.JSONDecodeError as e:
            raise VideoProcessingError(f"Failed to parse ffprobe output: {e}", "metadata")
        except Exception as e:
            logger.error(f"Failed to extract video metadata: {e}")
            raise VideoProcessingError(f"Failed to extract metadata: {e}", "metadata")

    def _parse_ffprobe_output(self, data: dict) -> VideoMetadata:
        """Parse ffprobe JSON output into VideoMetadata."""
        streams = data.get("streams", [])
        format_info = data.get("format", {})

        video_stream = None
        audio_stream = None

        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video" and video_stream is None:
                video_stream = stream
            elif codec_type == "audio" and audio_stream is None:
                audio_stream = stream

        if not video_stream:
            raise VideoProcessingError("No video stream found", "metadata")

        width = video_stream.get("width", 0)
        height = video_stream.get("height", 0)

        duration = 0.0
        if "duration" in format_info:
            duration = float(format_info["duration"])
        elif "duration" in video_stream:
            duration = float(video_stream["duration"])

        fps = None
        if "r_frame_rate" in video_stream:
            fps_str = video_stream["r_frame_rate"]
            if "/" in fps_str:
                num, den = fps_str.split("/")
                if int(den) > 0:
                    fps = float(num) / float(den)
            else:
                fps = float(fps_str)

        bitrate = None
        if "bit_rate" in format_info:
            bitrate = int(format_info["bit_rate"])
        elif "bit_rate" in video_stream:
            bitrate = int(video_stream["bit_rate"])

        audio_codec = None
        audio_bitrate = None
        audio_channels = None
        audio_sample_rate = None

        if audio_stream:
            audio_codec = audio_stream.get("codec_name")
            if "bit_rate" in audio_stream:
                audio_bitrate = int(audio_stream["bit_rate"])
            audio_channels = audio_stream.get("channels")
            if "sample_rate" in audio_stream:
                audio_sample_rate = int(audio_stream["sample_rate"])

        return VideoMetadata(
            width=width,
            height=height,
            duration=duration,
            codec=video_stream.get("codec_name"),
            bitrate=bitrate,
            fps=fps,
            audio_codec=audio_codec,
            audio_bitrate=audio_bitrate,
            audio_channels=audio_channels,
            audio_sample_rate=audio_sample_rate,
        )

    def is_supported(self, content_type: str) -> bool:
        """Check if content type is supported."""
        return content_type.lower() in self.SUPPORTED_FORMATS

    def get_duration(self, file_path: str) -> float:
        """
        Get video duration in seconds.
        
        Args:
            file_path: Path to video file
            
        Returns:
            Duration in seconds
        """
        metadata = self.get_metadata(file_path)
        return metadata.duration

    def get_dimensions(self, file_path: str) -> tuple:
        """
        Get video dimensions.
        
        Args:
            file_path: Path to video file
            
        Returns:
            Tuple of (width, height)
        """
        metadata = self.get_metadata(file_path)
        return metadata.width, metadata.height
