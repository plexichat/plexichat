"""
Media compression - Image and video compression for uploads.

Provides:
- Image compression and format conversion (WebP, JPEG)
- Video transcoding (H.264, H.265)
- Configurable quality levels
- Thumbnail generation
"""

from src.core.security.hardening import HardeningViolation, bounded_int

import os
import re
import subprocess
import tempfile
import shutil
import importlib.util
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

import utils.logger as logger
import utils.config as config
from ._result_cache import ResultCache, content_hash
from .processing import flatten_alpha_onto_white
from .processing.video import resolve_ffprobe_path


# Pillow's ~2x decompression-bomb threshold (~13400x13400 pixels). A crafted
# image can declare enormous dimensions in its header and make the decoder
# allocate gigabytes before a resize ever happens, so the compressor validates
# the pixel count itself instead of relying on Pillow's process-wide global.
DEFAULT_MAX_IMAGE_PIXELS = 178956970

# ffmpeg output is bounded by wall-clock time (5 minutes) but not by size. A
# small upload with a forged duration/geometry header can make ffmpeg emit far
# more data than the source; never load that file into memory unbounded.
MAX_TRANSCODE_OUTPUT_BYTES = 512 * 1024 * 1024


class CompressionQuality(Enum):
    """Compression quality presets."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ORIGINAL = "original"


class ImageFormat(Enum):
    """Output image formats."""

    WEBP = "webp"
    JPEG = "jpeg"
    PNG = "png"
    ORIGINAL = "original"


class VideoCodec(Enum):
    """Video codecs for transcoding."""

    H264 = "h264"
    H265 = "h265"
    VP9 = "vp9"
    ORIGINAL = "original"


@dataclass
class CompressionResult:
    """Result of compression operation."""

    success: bool
    data: Optional[bytes] = None
    original_size: int = 0
    compressed_size: int = 0
    format: Optional[str] = None
    error: Optional[str] = None

    @property
    def compression_ratio(self) -> float:
        """Calculate compression ratio."""
        if self.original_size == 0:
            return 0.0
        return 1.0 - (self.compressed_size / self.original_size)

    @property
    def savings_percent(self) -> float:
        """Calculate percentage savings."""
        return self.compression_ratio * 100


# Quality presets for images
IMAGE_QUALITY_PRESETS = {
    CompressionQuality.LOW: {"webp": 60, "jpeg": 65},
    CompressionQuality.MEDIUM: {"webp": 75, "jpeg": 80},
    CompressionQuality.HIGH: {"webp": 85, "jpeg": 90},
}

# CRF presets for video (lower = better quality, larger file)
VIDEO_CRF_PRESETS = {
    CompressionQuality.LOW: {"h264": 28, "h265": 32, "vp9": 35},
    CompressionQuality.MEDIUM: {"h264": 23, "h265": 28, "vp9": 31},
    CompressionQuality.HIGH: {"h264": 18, "h265": 23, "vp9": 25},
}


def _bounded_image_quality(value: object) -> int:
    """Coerce an image quality setting into Pillow's 1..100 range."""
    return bounded_int(value, "compression image quality", minimum=1, maximum=100)


# Quality presets are module constants. Bound them once here instead of
# re-validating the same literals on every compress() call.
_BOUNDED_IMAGE_QUALITY_PRESETS = {
    preset: {
        target: _bounded_image_quality(value) for target, value in targets.items()
    }
    for preset, targets in IMAGE_QUALITY_PRESETS.items()
}


class ImageCompressor:
    """Handles image compression and format conversion."""

    def __init__(self):
        """Initialize image compressor."""
        self._config = self._load_config()
        self._available = self._check_availability()
        # Snapshot the bounded parameters once. Validating config on every
        # upload did the same str/regex work N times and reopened the door to
        # a value changing mid-batch; bad config now fails fast at init.
        self._quality = _bounded_image_quality(self._config["quality"])
        self._max_dimension = bounded_int(
            self._config["max_dimension"],
            "compression max_dimension",
            minimum=1,
            maximum=16384,
        )
        self._max_pixels = bounded_int(
            self._config["max_pixels"],
            "compression max_pixels",
            minimum=1,
            maximum=2**31 - 1,
        )
        self._format = str(self._config["format"]).strip().lower()

    def _load_config(self) -> dict:
        """Load compression configuration."""
        media_config = config.get("media", {})
        compression_config = media_config.get("compression", {})
        image_config = compression_config.get("image", {})

        return {
            "enabled": compression_config.get("enabled", True),
            "format": image_config.get("format", "webp"),
            "quality": image_config.get("quality", 85),
            "max_dimension": image_config.get("max_dimension", 4096),
            "max_pixels": image_config.get("max_pixels", DEFAULT_MAX_IMAGE_PIXELS),
            "preserve_original": compression_config.get("preserve_original", False),
        }

    def _check_availability(self) -> bool:
        """Check if Pillow is available."""
        if importlib.util.find_spec("PIL") is not None:
            return True
        else:
            logger.debug(
                "Optional dependency Pillow is unavailable; image compression is disabled"
            )
            return False

    def dependency_available(self) -> bool:
        """Return whether Pillow is installed, independent of configuration."""
        return self._available

    def is_available(self) -> bool:
        """Check if compression is available."""
        return self._available and self._config["enabled"]

    def _reject_decompression_bomb(self, img) -> None:
        """Refuse an image whose header declares too many pixels.

        ``Image.open`` is lazy: it reads the header but not the pixel data, so
        checking ``img.size`` here rejects a decompression bomb *before*
        ``thumbnail()`` forces a full decode. The check is on total pixels (not
        width/height) so ordinary large images are still legitimately resized
        down to ``max_dimension``. Pillow's process-wide ``MAX_IMAGE_PIXELS``
        global is deliberately not touched -- it belongs to the shared decoder
        and would affect unrelated code paths.
        """
        width, height = img.size
        if width <= 0 or height <= 0:
            raise HardeningViolation("compression image has invalid dimensions")
        if width * height > self._max_pixels:
            raise HardeningViolation(
                f"compression image exceeds decompression-bomb limit of "
                f"{self._max_pixels} pixels"
            )

    def compress(
        self,
        image_data: bytes,
        quality: Optional[CompressionQuality] = None,
        output_format: Optional[ImageFormat] = None,
    ) -> CompressionResult:
        """
        Compress an image.

        Args:
            image_data: Raw image bytes
            quality: Quality preset (uses config default if None)
            output_format: Output format (uses config default if None)

        Returns:
            CompressionResult with compressed data
        """
        if not self.is_available():
            return CompressionResult(
                success=False,
                original_size=len(image_data),
                error="Image compression not available",
            )

        try:
            from PIL import Image
            import io

            original_size = len(image_data)

            # Open image. Image.open() is lazy, so this header-only step does
            # not decode pixels; validate before anything forces a decode.
            img = Image.open(io.BytesIO(image_data))
            self._reject_decompression_bomb(img)
            original_format = img.format

            # Determine output format
            if output_format is None or output_format == ImageFormat.ORIGINAL:
                fmt = self._format
            else:
                fmt = output_format.value

            # Determine quality from the pre-bounded defaults/presets.
            if quality is None or quality == CompressionQuality.ORIGINAL:
                q = self._quality
            else:
                presets = _BOUNDED_IMAGE_QUALITY_PRESETS.get(
                    quality, _BOUNDED_IMAGE_QUALITY_PRESETS[CompressionQuality.MEDIUM]
                )
                q = presets.get(fmt, 80)

            # Resize if too large -- max_dimension was bounded at init.
            max_dim = self._max_dimension
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

            # JPEG has no palette mode and cannot hold an alpha channel, so a
            # source carrying transparency must be composited onto white or
            # the alpha channel is silently dropped on re-encode. WebP is
            # left alone: it stores alpha natively, and flattening it would
            # throw away transparency the format was chosen to keep.
            #
            # The old inline composite handled this with a mask built for
            # ``RGBA``/``P`` sources only, so an ``LA`` (grayscale + alpha) or
            # ``PA`` source fell through with no mask at all and its alpha
            # channel was dropped -- a fully transparent region embedded as
            # its underlying colour (or black) instead of white.
            if fmt == "jpeg" and img.mode != "RGB":
                img = flatten_alpha_onto_white(img)
                if img.mode != "RGB":
                    img = img.convert("RGB")

            # Compress
            output = io.BytesIO()

            if fmt == "webp":
                img.save(output, format="WEBP", quality=q, method=4)
                mime_type = "image/webp"
            elif fmt == "jpeg":
                img.save(output, format="JPEG", quality=q, optimize=True)
                mime_type = "image/jpeg"
            elif fmt == "png":
                img.save(output, format="PNG", optimize=True)
                mime_type = "image/png"
            else:
                img.save(output, format=original_format or "PNG")
                mime_type = f"image/{(original_format or 'png').lower()}"

            compressed_data = output.getvalue()
            compressed_size = len(compressed_data)

            # Only use compressed version if it's smaller
            if (
                compressed_size >= original_size
                and not self._config["preserve_original"]
            ):
                return CompressionResult(
                    success=True,
                    data=image_data,
                    original_size=original_size,
                    compressed_size=original_size,
                    format=original_format,
                )

            logger.debug(
                f"Image compressed: {original_size} -> {compressed_size} bytes ({100 - (compressed_size / original_size * 100):.1f}% reduction)"
            )

            return CompressionResult(
                success=True,
                data=compressed_data,
                original_size=original_size,
                compressed_size=compressed_size,
                format=mime_type,
            )

        except Exception as e:
            logger.error(f"Image compression failed: {e}")
            return CompressionResult(
                success=False, original_size=len(image_data), error=str(e)
            )


_FFMPEG_PRESETS = frozenset(
    {
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
        "placebo",
    }
)


def _bounded_ffmpeg_preset(value: object) -> str:
    """Only a known x264/x265 preset may be passed to ffmpeg."""
    preset = str(value or "").strip().lower()
    if preset not in _FFMPEG_PRESETS:
        raise HardeningViolation("compression ffmpeg preset is not allowed")
    return preset


def _bounded_audio_bitrate(value: object) -> str:
    """Only a plain numeric k/m bitrate may be passed to ffmpeg."""
    bitrate = str(value or "").strip().lower()
    if not re.fullmatch(r"\d{1,4}[km]", bitrate):
        raise HardeningViolation("compression audio bitrate is not allowed")
    return bitrate


class VideoCompressor:
    """Handles video compression and transcoding."""

    def __init__(self):
        """Initialize video compressor."""
        self._config = self._load_config()
        self._ffmpeg_path = self._find_ffmpeg()
        # Validate every config-sourced ffmpeg argument once, at construction.
        # The bounded values (especially audio_bitrate) are what the command
        # builder below must use -- never the raw config strings.
        self._preset = _bounded_ffmpeg_preset(self._config["preset"])
        self._audio_bitrate = _bounded_audio_bitrate(self._config["audio_bitrate"])
        self._crf = bounded_int(
            self._config["crf"], "compression crf", minimum=0, maximum=63
        )
        self._codec = str(self._config["codec"]).strip().lower()
        # Transcoding is deterministic for a given payload + settings. Reuse a
        # bounded result set keyed by content hash so repeated uploads of the
        # same video do not spawn another ffmpeg subprocess.
        self._transcode_cache = ResultCache(
            maxsize=32,
            max_bytes=64 * 1024 * 1024,
            sizer=lambda result: max(1, len(result.data or b"")),
            max_inflight=2,
        )

    def _load_config(self) -> dict:
        """Load compression configuration."""
        media_config = config.get("media", {})
        compression_config = media_config.get("compression", {})
        video_config = compression_config.get("video", {})

        return {
            "enabled": video_config.get("enabled", True),
            "codec": video_config.get("codec", "h264"),
            "crf": video_config.get("crf", 23),
            "max_duration": video_config.get("max_duration", 600),  # 10 minutes
            "preset": video_config.get("preset", "medium"),
            "audio_bitrate": video_config.get("audio_bitrate", "128k"),
        }

    def _find_ffmpeg(self) -> Optional[str]:
        """Find ffmpeg executable."""
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            try:
                result = subprocess.run(
                    [ffmpeg_path, "-version"], capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    return ffmpeg_path
            except (subprocess.SubprocessError, FileNotFoundError):
                pass

        # Try common paths
        common_paths = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "C:\\ffmpeg\\bin\\ffmpeg.exe",
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path

        logger.debug(
            "Optional dependency ffmpeg is unavailable; video compression is disabled"
        )
        return None

    def dependency_available(self) -> bool:
        """Return whether ffmpeg is installed, independent of configuration."""
        return self._ffmpeg_path is not None

    def is_available(self) -> bool:
        """Check if compression is available."""
        return self._ffmpeg_path is not None and self._config["enabled"]

    def compress(
        self,
        video_data: bytes,
        quality: Optional[CompressionQuality] = None,
        codec: Optional[VideoCodec] = None,
    ) -> CompressionResult:
        """Compress a video, reusing a cached result for identical input.

        Before: every call spawned a fresh ffmpeg subprocess even when the same
        bytes and settings had already been transcoded. After: a successful
        result is memoized under the content hash; failures and unavailable
        dependencies are never cached.
        """
        if not self.is_available():
            return self._compress_uncached(video_data, quality, codec)

        settings = tuple((key, str(value)) for key, value in sorted(self._config.items()))
        key = (
            "transcode",
            content_hash(video_data),
            quality.value if quality is not None else None,
            codec.value if codec is not None else None,
            self._ffmpeg_path,
            settings,
        )
        cached = self._transcode_cache.get(key)
        if cached is not None:
            return cached

        result_box: list = []

        def compute():
            result = self._compress_uncached(video_data, quality, codec)
            result_box.append(result)
            # Returning None tells the cache not to retain this value: a
            # transient ffmpeg failure must not poison later attempts.
            if not result.success or result.data is None:
                return None
            return result

        cached = self._transcode_cache.get_or_compute(key, compute)
        return cached if cached is not None else result_box[0]

    def _compress_uncached(
        self,
        video_data: bytes,
        quality: Optional[CompressionQuality] = None,
        codec: Optional[VideoCodec] = None,
    ) -> CompressionResult:
        """
        Compress a video (uncached implementation).

        Args:
            video_data: Raw video bytes
            quality: Quality preset
            codec: Output codec

        Returns:
            CompressionResult with compressed data
        """
        if not self.is_available():
            return CompressionResult(
                success=False,
                original_size=len(video_data),
                error="Video compression not available",
            )

        original_size = len(video_data)

        # These were validated once at init; use the bounded snapshots so an
        # arbitrary preset/bitrate can never reach the ffmpeg command line.
        preset = self._preset
        audio_bitrate = self._audio_bitrate

        # Determine codec
        if codec is None or codec == VideoCodec.ORIGINAL:
            codec_name = self._codec
        else:
            codec_name = codec.value

        # Determine CRF (bounded at init)
        if quality is None or quality == CompressionQuality.ORIGINAL:
            crf = self._crf
        else:
            presets = VIDEO_CRF_PRESETS.get(
                quality, VIDEO_CRF_PRESETS[CompressionQuality.MEDIUM]
            )
            crf = presets.get(codec_name, 23)

        input_path: Optional[str] = None
        output_path: Optional[str] = None

        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as input_file:
                input_file.write(video_data)
                input_path = input_file.name

            output_path = input_path + ".compressed.mp4"

            # Build ffmpeg command
            cmd = [
                self._ffmpeg_path,
                "-i",
                input_path,
                "-y",  # Overwrite output
            ]

            # Codec settings
            if codec_name == "h264":
                cmd.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-crf",
                        str(crf),
                        "-preset",
                        preset,
                    ]
                )
            elif codec_name == "h265":
                cmd.extend(
                    [
                        "-c:v",
                        "libx265",
                        "-crf",
                        str(crf),
                        "-preset",
                        preset,
                    ]
                )
            elif codec_name == "vp9":
                cmd.extend(["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0"])

            # Audio settings
            cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate])

            # Output
            cmd.append(output_path)

            # Run ffmpeg
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                error = result.stderr.decode()[:500]
                logger.error(f"ffmpeg failed: {error}")
                return CompressionResult(
                    success=False,
                    original_size=original_size,
                    error=f"Transcoding failed: {error}",
                )

            # Read compressed output with a hard size cap. ffmpeg runs under a
            # 5-minute wall-clock timeout but that does not bound how many bytes
            # it writes: a forged input header can produce a huge file, and an
            # unbounded f.read() would pin all of it in memory.
            try:
                output_size = os.path.getsize(output_path)
            except OSError:
                output_size = 0
            if output_size > MAX_TRANSCODE_OUTPUT_BYTES:
                logger.error(
                    f"ffmpeg output too large: {output_size} bytes "
                    f"(limit {MAX_TRANSCODE_OUTPUT_BYTES})"
                )
                return CompressionResult(
                    success=False,
                    original_size=original_size,
                    error="Transcoded output exceeds size limit",
                )

            with open(output_path, "rb") as f:
                compressed_data = f.read(MAX_TRANSCODE_OUTPUT_BYTES + 1)
            if len(compressed_data) > MAX_TRANSCODE_OUTPUT_BYTES:
                # The file grew between the stat() and the read(); fail closed
                # instead of retaining an oversized buffer.
                logger.error("ffmpeg output grew past the read size cap")
                return CompressionResult(
                    success=False,
                    original_size=original_size,
                    error="Transcoded output exceeds size limit",
                )

            compressed_size = len(compressed_data)

            # Cleanup
            os.unlink(input_path)
            os.unlink(output_path)

            logger.debug(
                f"Video compressed: {original_size} -> {compressed_size} bytes ({100 - (compressed_size / original_size * 100):.1f}% reduction)"
            )

            return CompressionResult(
                success=True,
                data=compressed_data,
                original_size=original_size,
                compressed_size=compressed_size,
                format="video/mp4",
            )

        except subprocess.TimeoutExpired:
            logger.error("Video compression timed out")
            return CompressionResult(
                success=False,
                original_size=original_size,
                error="Compression timed out",
            )
        except Exception as e:
            logger.error(f"Video compression failed: {e}")
            return CompressionResult(
                success=False, original_size=original_size, error=str(e)
            )
        finally:
            # Cleanup temp files
            for path in [input_path, output_path]:
                try:
                    if path and os.path.exists(path):
                        os.unlink(path)
                except OSError as unlink_error:
                    logger.debug(f"compression temp unlink failed: {unlink_error}")


class CompressionManager:
    """Unified compression manager for all media types."""

    def __init__(self):
        """Initialize compression manager."""
        self._image_compressor = ImageCompressor()
        self._video_compressor = VideoCompressor()
        self._config = self._load_config()
        self._enabled = bool(self._config.get("enabled", True))
        # Keep this probe consistent with ``VideoProcessor``: deployments may
        # provide ffprobe outside PATH through media.video_processing.ffprobe_path.
        # Resolve it once at manager construction so status requests remain cheap.
        self._ffprobe_path = resolve_ffprobe_path()
        self._ffprobe_available = self._ffprobe_path is not None

    def get_ffprobe_path(self) -> Optional[str]:
        """Return the resolved ffprobe path used for capability reporting."""
        return self._ffprobe_path

    def _load_config(self) -> dict:
        """Load compression configuration."""
        media_config = config.get("media", {})
        return media_config.get("compression", {"enabled": True})

    def is_enabled(self) -> bool:
        """Check if compression is enabled (snapshotted at construction)."""
        return self._enabled

    def compress(
        self,
        file_data: bytes,
        content_type: str,
        quality: Optional[CompressionQuality] = None,
    ) -> CompressionResult:
        """
        Compress a file based on its content type.

        Args:
            file_data: Raw file bytes
            content_type: MIME type
            quality: Quality preset

        Returns:
            CompressionResult
        """
        if not self.is_enabled():
            return CompressionResult(
                success=True,
                data=file_data,
                original_size=len(file_data),
                compressed_size=len(file_data),
            )

        ct_lower = content_type.lower()

        if ct_lower.startswith("image/"):
            return self._image_compressor.compress(file_data, quality)
        elif ct_lower.startswith("video/"):
            return self._video_compressor.compress(file_data, quality)
        else:
            # No compression for other types
            return CompressionResult(
                success=True,
                data=file_data,
                original_size=len(file_data),
                compressed_size=len(file_data),
            )

    def _video_processor_available(self) -> bool:
        """Return the cached ffprobe availability result."""
        return self._ffprobe_available

    def get_status(self) -> Dict[str, Any]:
        """Get compression and optional media dependency status."""
        return {
            "enabled": self.is_enabled(),
            "image_compression": self._image_compressor.is_available(),
            "video_compression": self._video_compressor.is_available(),
            "pillow_available": self._image_compressor.dependency_available(),
            "ffmpeg_available": self._video_compressor.dependency_available(),
            "ffprobe_available": self._video_processor_available(),
            "ffprobe_path": self.get_ffprobe_path(),
        }
