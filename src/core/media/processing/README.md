# Media processing

Use the media processing package to transform and inspect images and videos that users upload to Plexichat.

## Overview

This package provides two processors:

- `ImageProcessor` (`images.py:35`) — resize, thumbnail, format conversion, and metadata extraction with Pillow. Includes protection against decompression bombs.
- `VideoProcessor` (`video.py:55`) — metadata extraction with `ffprobe` with configurable timeout and file-size limits.

Both processors validate file dimensions and enforce limits from `config.yaml` under `media.image_processing` and `media.video_processing`.

## Prerequisites

Install Pillow for image operations. Install `ffprobe` (part of FFmpeg) for video operations. Video metadata extraction is disabled if `ffprobe` is not available.

```bash
pip install Pillow
```

## Process images

Create an `ImageProcessor` and call its methods. The processor sets `Pillow.Image.MAX_IMAGE_PIXELS` from config to prevent decompression bombs.

### Get image metadata

```python
from src.core.media.processing import ImageProcessor

processor = ImageProcessor(quality=85, optimize=True)
metadata = processor.get_metadata(image_data)

print(metadata.width, metadata.height, metadata.format)
print(metadata.has_alpha, metadata.animated)
```

`get_metadata()` (`images.py:117`) returns an `ImageMetadata` object with `width`, `height`, `format`, `mode`, `has_alpha`, `animated`, `frame_count`, and `exif`.

### Create thumbnails

```python
# Single thumbnail
thumb_data, width, height = processor.create_thumbnail(
    image_data, size=128, output_format="JPEG"
)

# Multiple thumbnails at standard sizes
thumbs = processor.create_thumbnails(image_data, sizes=[128, 256, 512])
# Returns {128: (bytes, w, h), 256: (bytes, w, h), ...}
```

`create_thumbnail()` (`images.py:170`) and `create_thumbnails()` (`images.py:227`) maintain aspect ratio by default and composite transparent images onto a white background when you output JPEG.

### Resize an image

```python
resized, width, height = processor.resize(
    image_data, width=800, maintain_aspect=True, output_format="JPEG"
)
```

You must specify at least one of `width` or `height` (`images.py:290`). If you omit `output_format`, the processor keeps the original format.

### Convert format

```python
webp_data = processor.convert_format(image_data, output_format="WEBP")
```

### Strip metadata

Remove EXIF and other metadata before you store or serve the image:

```python
clean_data = processor.strip_metadata(image_data)
```

### Check support

```python
if processor.is_supported("image/jpeg"):
    print("Supported")
```

Supported content types are defined in `images.py:38`: `image/jpeg`, `image/png`, `image/gif`, `image/webp`, `image/bmp`, and `image/tiff`.

### Security limits

Configure limits in `config.yaml` or pass them to the constructor:

| Parameter | Default | Description |
|---|---|---|
| `max_dimension` | `16384` | Maximum width or height in pixels (`images.py:21`) |
| `max_pixels` | `178956970` | Maximum total pixels (`images.py:22`) |

The processor raises `ImageProcessingError` when an image exceeds these limits.

## Process videos

### Check availability

`ffprobe` is optional. Check availability before you call metadata methods:

```python
from src.core.media.processing import VideoProcessor

processor = VideoProcessor()
if not processor.is_available():
    print("ffprobe not found — video metadata disabled")
```

The processor resolves `ffprobe` from `media.video_processing.ffprobe_path` or from `PATH` (`video.py:36`).

### Extract metadata

```python
# From a file path
metadata = processor.get_metadata("/tmp/upload.mp4")

# From bytes (checks size limit first)
metadata = processor.get_metadata_from_bytes(video_data)

print(metadata.width, metadata.height, metadata.duration)
print(metadata.codec, metadata.fps, metadata.bitrate)
print(metadata.audio_codec, metadata.audio_channels)
```

`get_metadata_from_bytes()` (`video.py:154`) rejects files larger than `max_size_for_metadata` (default 500 MB, `video.py:24`) and raises `VideoProcessingError` on timeout or parse failure.

### Helpers

```python
duration = processor.get_duration("/tmp/upload.mp4")
width, height = processor.get_dimensions("/tmp/upload.mp4")
```

Supported content types are defined in `video.py:58`: `video/mp4`, `video/webm`, `video/ogg`, `video/quicktime`, `video/x-msvideo`, `video/x-matroska`, `video/x-flv`, `video/3gpp`, and `video/3gpp2`.

### Security limits

| Parameter | Default | Description |
|---|---|---|
| `ffprobe_timeout` | `30` seconds | Timeout for `ffprobe` subprocess (`video.py:23`) |
| `max_size_for_metadata` | `500` MB | Maximum bytes for `get_metadata_from_bytes()` |

## Error handling

Both processors raise typed errors that include an operation name:

```python
from src.core.media.exceptions import ImageProcessingError, VideoProcessingError

try:
    processor.create_thumbnail(image_data, size=256)
except ImageProcessingError as e:
    print(f"Failed during {e.operation}: {e}")
```

## Related code

- `images.py:35` — `ImageProcessor` implementation
- `video.py:55` — `VideoProcessor` implementation and `resolve_ffprobe_path()`
- `__init__.py:1` — public exports
