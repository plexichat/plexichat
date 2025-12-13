"""
Media compression - Image and video compression for uploads.

Provides:
- Image compression and format conversion (WebP, JPEG)
- Video transcoding (H.264, H.265)
- Configurable quality levels
- Thumbnail generation
"""

import os
import subprocess
import tempfile
import importlib.util
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

import utils.logger as logger
import utils.config as config


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


class ImageCompressor:
    """Handles image compression and format conversion."""

    def __init__(self):
        """Initialize image compressor."""
        self._config = self._load_config()
        self._available = self._check_availability()

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
            "preserve_original": compression_config.get("preserve_original", False),
        }

    def _check_availability(self) -> bool:
        """Check if Pillow is available."""
        if importlib.util.find_spec("PIL") is not None:
            return True
        else:
            logger.warning("Pillow not installed - image compression unavailable")
            return False

    def is_available(self) -> bool:
        """Check if compression is available."""
        return self._available and self._config["enabled"]

    def compress(
        self,
        image_data: bytes,
        quality: Optional[CompressionQuality] = None,
        output_format: Optional[ImageFormat] = None
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
                error="Image compression not available"
            )

        try:
            from PIL import Image
            import io

            original_size = len(image_data)

            # Open image
            img = Image.open(io.BytesIO(image_data))
            original_format = img.format

            # Determine output format
            if output_format is None or output_format == ImageFormat.ORIGINAL:
                fmt = self._config["format"]
            else:
                fmt = output_format.value

            # Determine quality
            if quality is None or quality == CompressionQuality.ORIGINAL:
                q = self._config["quality"]
            else:
                presets = IMAGE_QUALITY_PRESETS.get(quality, IMAGE_QUALITY_PRESETS[CompressionQuality.MEDIUM])
                q = presets.get(fmt, 80)

            # Resize if too large
            max_dim = self._config["max_dimension"]
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

            # Convert to RGB if necessary (for JPEG/WebP)
            if fmt in ("jpeg", "webp") and img.mode in ("RGBA", "P"):
                # Create white background for transparency
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[3] if len(img.split()) == 4 else None)
                img = background
            elif fmt == "jpeg" and img.mode != "RGB":
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
            if compressed_size >= original_size and not self._config["preserve_original"]:
                return CompressionResult(
                    success=True,
                    data=image_data,
                    original_size=original_size,
                    compressed_size=original_size,
                    format=original_format
                )

            logger.debug(f"Image compressed: {original_size} -> {compressed_size} bytes ({100 - (compressed_size/original_size*100):.1f}% reduction)")

            return CompressionResult(
                success=True,
                data=compressed_data,
                original_size=original_size,
                compressed_size=compressed_size,
                format=mime_type
            )

        except Exception as e:
            logger.error(f"Image compression failed: {e}")
            return CompressionResult(
                success=False,
                original_size=len(image_data),
                error=str(e)
            )


class VideoCompressor:
    """Handles video compression and transcoding."""

    def __init__(self):
        """Initialize video compressor."""
        self._config = self._load_config()
        self._ffmpeg_path = self._find_ffmpeg()

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
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                return "ffmpeg"
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

        logger.warning("ffmpeg not found - video compression unavailable")
        return None

    def is_available(self) -> bool:
        """Check if compression is available."""
        return self._ffmpeg_path is not None and self._config["enabled"]

    def compress(
        self,
        video_data: bytes,
        quality: Optional[CompressionQuality] = None,
        codec: Optional[VideoCodec] = None
    ) -> CompressionResult:
        """
        Compress a video.
        
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
                error="Video compression not available"
            )

        original_size = len(video_data)

        # Determine codec
        if codec is None or codec == VideoCodec.ORIGINAL:
            codec_name = self._config["codec"]
        else:
            codec_name = codec.value

        # Determine CRF
        if quality is None or quality == CompressionQuality.ORIGINAL:
            crf = self._config["crf"]
        else:
            presets = VIDEO_CRF_PRESETS.get(quality, VIDEO_CRF_PRESETS[CompressionQuality.MEDIUM])
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
                "-i", input_path,
                "-y",  # Overwrite output
            ]

            # Codec settings
            if codec_name == "h264":
                cmd.extend(["-c:v", "libx264", "-crf", str(crf), "-preset", self._config["preset"]])
            elif codec_name == "h265":
                cmd.extend(["-c:v", "libx265", "-crf", str(crf), "-preset", self._config["preset"]])
            elif codec_name == "vp9":
                cmd.extend(["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0"])

            # Audio settings
            cmd.extend(["-c:a", "aac", "-b:a", self._config["audio_bitrate"]])

            # Output
            cmd.append(output_path)

            # Run ffmpeg
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300  # 5 minute timeout
            )

            if result.returncode != 0:
                error = result.stderr.decode()[:500]
                logger.error(f"ffmpeg failed: {error}")
                return CompressionResult(
                    success=False,
                    original_size=original_size,
                    error=f"Transcoding failed: {error}"
                )

            # Read compressed output
            with open(output_path, "rb") as f:
                compressed_data = f.read()

            compressed_size = len(compressed_data)

            # Cleanup
            os.unlink(input_path)
            os.unlink(output_path)

            logger.debug(f"Video compressed: {original_size} -> {compressed_size} bytes ({100 - (compressed_size/original_size*100):.1f}% reduction)")

            return CompressionResult(
                success=True,
                data=compressed_data,
                original_size=original_size,
                compressed_size=compressed_size,
                format="video/mp4"
            )

        except subprocess.TimeoutExpired:
            logger.error("Video compression timed out")
            return CompressionResult(
                success=False,
                original_size=original_size,
                error="Compression timed out"
            )
        except Exception as e:
            logger.error(f"Video compression failed: {e}")
            return CompressionResult(
                success=False,
                original_size=original_size,
                error=str(e)
            )
        finally:
            # Cleanup temp files
            for path in [input_path, output_path]:
                try:
                    if path and os.path.exists(path):
                        os.unlink(path)
                except Exception:
                    pass


class CompressionManager:
    """Unified compression manager for all media types."""

    def __init__(self):
        """Initialize compression manager."""
        self._image_compressor = ImageCompressor()
        self._video_compressor = VideoCompressor()
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load compression configuration."""
        media_config = config.get("media", {})
        return media_config.get("compression", {"enabled": True})

    def is_enabled(self) -> bool:
        """Check if compression is enabled."""
        return self._config.get("enabled", True)

    def compress(
        self,
        file_data: bytes,
        content_type: str,
        quality: Optional[CompressionQuality] = None
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
                compressed_size=len(file_data)
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
                compressed_size=len(file_data)
            )

    def get_status(self) -> Dict[str, Any]:
        """Get compression system status."""
        return {
            "enabled": self.is_enabled(),
            "image_compression": self._image_compressor.is_available(),
            "video_compression": self._video_compressor.is_available(),
        }
