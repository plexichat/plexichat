"""Image processing mixin for the avatars module."""

import io
import hashlib
from typing import Tuple

import utils.logger as logger

from .protocol import AvatarProtocol


class AvatarProcessingMixin(AvatarProtocol):
    """Mixin handling image processing, validation, and resizing."""

    def _validate_content_type(self, content_type: str) -> bool:
        """Validate content type is allowed.

        SECURITY (Agent7 L1): :mod:`svg_guard` block-list applies regardless
        of operator configuration - an operator must not be able to opt into
        SVG/HTML/JS avatar types via ``allowed_types``.
        """
        from .svg_guard import is_blocked_avatar_content_type

        if is_blocked_avatar_content_type(content_type):
            return False
        allowed = self._get_allowed_types()
        return content_type.lower() in [t.lower() for t in allowed]

    def _detect_content_type(self, image_data: bytes, fallback: str) -> str:
        """Detect actual content type from magic bytes."""
        signatures = {
            b"\xff\xd8\xff": "image/jpeg",
            b"\x89PNG\r\n\x1a\n": "image/png",
            b"GIF87a": "image/gif",
            b"GIF89a": "image/gif",
            b"RIFF": "image/webp",
        }

        for sig, mime in signatures.items():
            if image_data.startswith(sig):
                if sig == b"RIFF":
                    if len(image_data) > 12 and image_data[8:12] == b"WEBP":
                        return "image/webp"
                    return fallback
                return mime

        return fallback

    def _process_image(
        self, image_data: bytes, content_type: str
    ) -> Tuple[bytes, int, int, bool]:
        """
        Process and resize image if needed.

        Returns: (processed_bytes, width, height, is_animated)
        """
        # SECURITY (Agent7 L1): reject SVG / active markup BEFORE anything
        # else - including the Pillow-missing verbatim path below, which would
        # otherwise store the payload unsanitized. This also catches
        # MIME-spoofed SVG (e.g. "<svg ..." bytes claimed as image/png).
        from .svg_guard import (
            is_blocked_avatar_content_type,
            is_unsafe_upload_payload,
        )

        if is_blocked_avatar_content_type(content_type) or is_unsafe_upload_payload(
            content_type, image_data
        ):
            raise ValueError(
                f"Content type '{content_type}' is not allowed for avatars: "
                "SVG and active markup are never accepted as avatars."
            )
        try:
            from PIL import Image
        except ImportError:
            logger.warning("Pillow not installed, storing avatar without processing")
            return image_data, 0, 0, False

        # Security: Prevent decompression bombs
        max_pixels = self._get_config("max_pixels", 178956970)
        Image.MAX_IMAGE_PIXELS = max_pixels

        # Do not allow images with more than 16k width/height
        max_dim = self._get_config("max_dimension", 16384)

        # Detect actual content type from bytes to prevent spoofing
        actual_type = self._detect_content_type(image_data, content_type)
        if actual_type != content_type:
            logger.info(
                f"Avatar: Detected actual type {actual_type} for file claimed as {content_type}"
            )
            content_type = actual_type

        # Open image (lazy)
        try:
            img = Image.open(io.BytesIO(image_data))

            # Security: Validate dimensions before processing
            width, height = img.size
            if width > max_dim or height > max_dim:
                raise ValueError(
                    f"Image dimensions ({width}x{height}) exceed maximum allowed ({max_dim}x{max_dim})"
                )

            if width * height > max_pixels:
                raise ValueError(
                    f"Image has too many pixels ({width * height}) - maximum is {max_pixels}"
                )

            original_format = img.format
            n_frames = getattr(img, "n_frames", 1)
            is_animated = bool(getattr(img, "is_animated", False)) or (n_frames > 1)
            # Capture the animation loop count while positioned on the first
            # frame: Pillow keeps the loop extension on frame 0, so reading
            # img.info after seeking to later frames may lose it.
            loop_count = img.info.get("loop", 0)
        except Exception as e:
            if isinstance(e, ValueError):
                raise e
            logger.error(f"Failed to open avatar image: {e}")
            raise ValueError(f"Invalid image file: {e}")

        max_size = self._get_max_size()

        # Check if resize needed
        if width > max_size or height > max_size:
            # Calculate new dimensions maintaining aspect ratio
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))

            if is_animated and original_format == "GIF":
                # Handle animated GIF - resize all frames
                frames = []
                durations = []

                try:
                    for frame_num in range(n_frames):
                        img.seek(frame_num)
                        frame = img.copy()
                        frame = frame.resize(
                            (new_width, new_height), Image.Resampling.LANCZOS
                        )
                        frames.append(frame)
                        durations.append(img.info.get("duration", 100))

                    # Save animated GIF
                    output = io.BytesIO()
                    frames[0].save(
                        output,
                        format="GIF",
                        save_all=True,
                        append_images=frames[1:],
                        duration=durations,
                        loop=loop_count,
                    )
                    return output.getvalue(), new_width, new_height, True
                except Exception as e:
                    logger.warning(
                        f"Failed to process animated GIF: {e}, using first frame"
                    )
                    img.seek(0)
                    is_animated = False

            if is_animated and original_format == "WEBP":
                # M2: animated WebP needed a resize - keep ALL frames instead
                # of collapsing to the first one.
                frames = []
                durations = []

                try:
                    for frame_num in range(n_frames):
                        img.seek(frame_num)
                        frame = img.copy()
                        frame = frame.resize(
                            (new_width, new_height), Image.Resampling.LANCZOS
                        )
                        frames.append(frame)
                        durations.append(img.info.get("duration", 100))

                    output = io.BytesIO()
                    frames[0].save(
                        output,
                        format="WEBP",
                        save_all=True,
                        append_images=frames[1:],
                        duration=durations,
                        loop=loop_count,
                        quality=90,
                    )
                    return output.getvalue(), new_width, new_height, True
                except Exception as e:
                    logger.warning(
                        f"Failed to process animated WebP: {e}, using first frame"
                    )
                    img.seek(0)
                    is_animated = False

            # Resize static image
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            width, height = new_width, new_height

        # Convert to appropriate format for output
        output = io.BytesIO()

        if content_type == "image/gif" and is_animated:
            # M2: Pillow only writes ONE frame unless save_all + append_images
            # are given, so an animated GIF that needed no resize previously
            # lost every frame after the first. Re-encode all frames.
            img.seek(0)
            frames = []
            durations = []
            for frame_num in range(n_frames):
                img.seek(frame_num)
                # Copying the frame keeps its palette + transparency info,
                # so transparent regions survive the re-encode.
                frames.append(img.copy())
                durations.append(img.info.get("duration", 100))
            try:
                frames[0].save(
                    output,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=loop_count,
                )
                return output.getvalue(), width, height, True
            except Exception as e:
                logger.warning(f"Failed to save animated GIF: {e}, using first frame")
                img.seek(0)
                is_animated = False
        elif content_type == "image/gif":
            # M4: a static GIF keeps its container (and any transparency)
            # instead of being silently re-encoded to lossy JPEG. Palette
            # transparency is preserved via the GIF disposal of the info dict.
            img.save(output, format="GIF", save_all=False)
        elif content_type == "image/png" or img.mode == "RGBA":
            img.save(output, format="PNG", optimize=True)
            content_type = "image/png"
        elif content_type == "image/webp":
            if is_animated and n_frames > 1:
                # M2: animated WebP must also keep all frames. Durations are
                # collected per frame: reusing the last frame's info for every
                # frame would mistime the animation.
                img.seek(0)
                frames = []
                durations = []
                for frame_num in range(n_frames):
                    img.seek(frame_num)
                    frames.append(img.copy())
                    durations.append(img.info.get("duration", 100))
                try:
                    frames[0].save(
                        output,
                        format="WEBP",
                        save_all=True,
                        append_images=frames[1:],
                        duration=durations,
                        loop=loop_count,
                        quality=90,
                    )
                    return output.getvalue(), width, height, True
                except Exception as e:
                    logger.warning(
                        f"Failed to save animated WebP: {e}, using first frame"
                    )
                    img.seek(0)
            else:
                img.save(output, format="WEBP", quality=90)
        else:
            # JPEG has no palette mode and cannot hold an alpha channel, so
            # transparency must be composited before the re-encode. The old
            # ``convert("RGB")`` silently dropped the alpha channel instead --
            # a fully transparent black region embedded as solid black rather
            # than the white background a viewer shows. ``LA`` sources were
            # not converted at all and failed to save.
            from src.core.media.processing.images import flatten_alpha_onto_white

            img = flatten_alpha_onto_white(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(output, format="JPEG", quality=90, optimize=True)
            content_type = "image/jpeg"

        return output.getvalue(), width, height, is_animated

    def _compute_checksum(self, data: bytes) -> str:
        """Compute SHA-256 checksum."""
        return hashlib.sha256(data).hexdigest()
