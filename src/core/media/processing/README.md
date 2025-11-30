# Media Processing

Image and video processing utilities.

## Components

- `images.py` - ImageProcessor for resizing, thumbnails, format conversion
- `video.py` - VideoProcessor for transcoding and thumbnail extraction

## Usage

```python
from src.core.media.processing import ImageProcessor, VideoProcessor

processor = ImageProcessor()
thumbnail = processor.create_thumbnail(image_data, size=(128, 128))
```
