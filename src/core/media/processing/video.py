"""
Video metadata extraction using ffprobe.
"""

import json
import subprocess
import shutil
from typing import Optional

import utils.logger as logger

from ..models import VideoMetadata
from ..exceptions import VideoProcessingError


class VideoProcessor:
    """Video metadata extraction using ffprobe."""
    
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
    
    def __init__(self, ffprobe_path: Optional[str] = None):
        """
        Initialize video processor.
        
        Args:
            ffprobe_path: Path to ffprobe executable (auto-detected if None)
        """
        self._ffprobe_path = ffprobe_path or self._find_ffprobe()
        
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
                timeout=30,
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
                "-i", "pipe:0",
            ]
            
            result = subprocess.run(
                cmd,
                input=video_data,
                capture_output=True,
                timeout=30,
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
