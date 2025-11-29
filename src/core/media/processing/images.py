"""
Image processing utilities using Pillow.
"""

import io
from typing import Optional, Tuple, List, Dict, Any

import utils.logger as logger

from ..models import ImageMetadata, ThumbnailSize
from ..exceptions import ImageProcessingError


class ImageProcessor:
    """Image processing using Pillow."""
    
    SUPPORTED_FORMATS = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/gif": "GIF",
        "image/webp": "WEBP",
        "image/bmp": "BMP",
        "image/tiff": "TIFF",
    }
    
    FORMAT_EXTENSIONS = {
        "JPEG": ".jpg",
        "PNG": ".png",
        "GIF": ".gif",
        "WEBP": ".webp",
        "BMP": ".bmp",
        "TIFF": ".tiff",
    }
    
    def __init__(self, quality: int = 85, optimize: bool = True):
        """
        Initialize image processor.
        
        Args:
            quality: JPEG/WebP quality (1-100)
            optimize: Enable optimization for output
        """
        self._quality = quality
        self._optimize = optimize
        
        try:
            from PIL import Image
            self._Image = Image
        except ImportError:
            raise ImageProcessingError(
                "Pillow is required for image processing. Install with: pip install Pillow",
                "init"
            )
    
    def get_metadata(self, image_data: bytes) -> ImageMetadata:
        """
        Extract metadata from image.
        
        Args:
            image_data: Raw image bytes
            
        Returns:
            ImageMetadata object
        """
        try:
            img = self._Image.open(io.BytesIO(image_data))
            
            has_alpha = img.mode in ("RGBA", "LA", "PA") or (
                img.mode == "P" and "transparency" in img.info
            )
            
            animated = getattr(img, "is_animated", False)
            frame_count = getattr(img, "n_frames", 1)
            
            exif_data = None
            if hasattr(img, "_getexif") and img._getexif():
                raw_exif = img._getexif()
                exif_data = {}
                for tag_id, value in raw_exif.items():
                    try:
                        from PIL.ExifTags import TAGS
                        tag = TAGS.get(tag_id, tag_id)
                        if isinstance(value, bytes):
                            continue
                        exif_data[tag] = value
                    except Exception:
                        pass
            
            return ImageMetadata(
                width=img.width,
                height=img.height,
                format=img.format or "UNKNOWN",
                mode=img.mode,
                has_alpha=has_alpha,
                animated=animated,
                frame_count=frame_count,
                exif=exif_data,
            )
        except Exception as e:
            logger.error(f"Failed to extract image metadata: {e}")
            raise ImageProcessingError(f"Failed to extract metadata: {e}", "metadata")
    
    def create_thumbnail(
        self,
        image_data: bytes,
        size: int,
        output_format: str = "JPEG",
        maintain_aspect: bool = True,
    ) -> Tuple[bytes, int, int]:
        """
        Create a thumbnail of the specified size.
        
        Args:
            image_data: Raw image bytes
            size: Maximum dimension (width or height)
            output_format: Output format (JPEG, PNG, WEBP)
            maintain_aspect: Maintain aspect ratio
            
        Returns:
            Tuple of (thumbnail bytes, width, height)
        """
        try:
            img = self._Image.open(io.BytesIO(image_data))
            
            if img.mode == "P" and "transparency" in img.info:
                img = img.convert("RGBA")
            
            if maintain_aspect:
                img.thumbnail((size, size), self._Image.Resampling.LANCZOS)
            else:
                img = img.resize((size, size), self._Image.Resampling.LANCZOS)
            
            if output_format == "JPEG" and img.mode in ("RGBA", "LA", "P"):
                background = self._Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            
            output = io.BytesIO()
            save_kwargs = {"format": output_format}
            
            if output_format in ("JPEG", "WEBP"):
                save_kwargs["quality"] = self._quality
            if self._optimize and output_format in ("JPEG", "PNG"):
                save_kwargs["optimize"] = True
            
            img.save(output, **save_kwargs)
            
            return output.getvalue(), img.width, img.height
        except Exception as e:
            logger.error(f"Failed to create thumbnail: {e}")
            raise ImageProcessingError(f"Failed to create thumbnail: {e}", "thumbnail")
    
    def create_thumbnails(
        self,
        image_data: bytes,
        sizes: List[int] = None,
        output_format: str = "JPEG",
    ) -> Dict[int, Tuple[bytes, int, int]]:
        """
        Create multiple thumbnails at standard sizes.
        
        Args:
            image_data: Raw image bytes
            sizes: List of sizes (defaults to ThumbnailSize values)
            output_format: Output format
            
        Returns:
            Dict mapping size to (bytes, width, height)
        """
        if sizes is None:
            sizes = [s.value for s in ThumbnailSize]
        
        results = {}
        for size in sizes:
            try:
                results[size] = self.create_thumbnail(image_data, size, output_format)
            except ImageProcessingError:
                logger.warning(f"Failed to create thumbnail at size {size}")
        
        return results
    
    def resize(
        self,
        image_data: bytes,
        width: Optional[int] = None,
        height: Optional[int] = None,
        maintain_aspect: bool = True,
        output_format: Optional[str] = None,
    ) -> Tuple[bytes, int, int]:
        """
        Resize image to specified dimensions.
        
        Args:
            image_data: Raw image bytes
            width: Target width (None to calculate from height)
            height: Target height (None to calculate from width)
            maintain_aspect: Maintain aspect ratio
            output_format: Output format (None to keep original)
            
        Returns:
            Tuple of (resized bytes, width, height)
        """
        if width is None and height is None:
            raise ImageProcessingError("Must specify width or height", "resize")
        
        try:
            img = self._Image.open(io.BytesIO(image_data))
            original_format = img.format
            
            if maintain_aspect:
                orig_w, orig_h = img.size
                if width and height:
                    ratio = min(width / orig_w, height / orig_h)
                    new_w = int(orig_w * ratio)
                    new_h = int(orig_h * ratio)
                elif width:
                    ratio = width / orig_w
                    new_w = width
                    new_h = int(orig_h * ratio)
                else:
                    ratio = height / orig_h
                    new_w = int(orig_w * ratio)
                    new_h = height
            else:
                new_w = width or img.width
                new_h = height or img.height
            
            img = img.resize((new_w, new_h), self._Image.Resampling.LANCZOS)
            
            out_format = output_format or original_format or "PNG"
            
            if out_format == "JPEG" and img.mode in ("RGBA", "LA", "P"):
                background = self._Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            
            output = io.BytesIO()
            save_kwargs = {"format": out_format}
            
            if out_format in ("JPEG", "WEBP"):
                save_kwargs["quality"] = self._quality
            if self._optimize and out_format in ("JPEG", "PNG"):
                save_kwargs["optimize"] = True
            
            img.save(output, **save_kwargs)
            
            return output.getvalue(), img.width, img.height
        except Exception as e:
            logger.error(f"Failed to resize image: {e}")
            raise ImageProcessingError(f"Failed to resize image: {e}", "resize")
    
    def convert_format(
        self,
        image_data: bytes,
        output_format: str,
        quality: Optional[int] = None,
    ) -> bytes:
        """
        Convert image to different format.
        
        Args:
            image_data: Raw image bytes
            output_format: Target format (JPEG, PNG, WEBP, etc.)
            quality: Quality for lossy formats
            
        Returns:
            Converted image bytes
        """
        try:
            img = self._Image.open(io.BytesIO(image_data))
            
            if output_format == "JPEG" and img.mode in ("RGBA", "LA", "P"):
                background = self._Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            
            output = io.BytesIO()
            save_kwargs = {"format": output_format}
            
            if output_format in ("JPEG", "WEBP"):
                save_kwargs["quality"] = quality or self._quality
            if self._optimize and output_format in ("JPEG", "PNG"):
                save_kwargs["optimize"] = True
            
            img.save(output, **save_kwargs)
            
            return output.getvalue()
        except Exception as e:
            logger.error(f"Failed to convert image format: {e}")
            raise ImageProcessingError(f"Failed to convert format: {e}", "convert")
    
    def strip_metadata(self, image_data: bytes, output_format: Optional[str] = None) -> bytes:
        """
        Strip EXIF and other metadata from image.
        
        Args:
            image_data: Raw image bytes
            output_format: Output format (None to keep original)
            
        Returns:
            Image bytes without metadata
        """
        try:
            img = self._Image.open(io.BytesIO(image_data))
            original_format = img.format
            
            data = list(img.getdata())
            img_no_exif = self._Image.new(img.mode, img.size)
            img_no_exif.putdata(data)
            
            out_format = output_format or original_format or "PNG"
            
            output = io.BytesIO()
            save_kwargs = {"format": out_format}
            
            if out_format in ("JPEG", "WEBP"):
                save_kwargs["quality"] = self._quality
            
            img_no_exif.save(output, **save_kwargs)
            
            return output.getvalue()
        except Exception as e:
            logger.error(f"Failed to strip metadata: {e}")
            raise ImageProcessingError(f"Failed to strip metadata: {e}", "strip_metadata")
    
    def is_supported(self, content_type: str) -> bool:
        """Check if content type is supported."""
        return content_type.lower() in self.SUPPORTED_FORMATS
    
    def get_format_extension(self, format_name: str) -> str:
        """Get file extension for format."""
        return self.FORMAT_EXTENSIONS.get(format_name.upper(), ".bin")
