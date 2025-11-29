# Media Module

File upload, storage, and processing system for PlexiChat API supporting local filesystem and S3-compatible storage backends.

## Features

- File upload with configurable storage backends (local filesystem, S3-compatible)
- Image processing with Pillow (thumbnails, resizing, format conversion)
- Video metadata extraction using ffprobe
- Secure URL signing with HMAC and expiration
- External URL proxy with caching
- Content-type validation and configurable file size limits
- Malware scanning interface for ClamAV
- Integration with messaging module for attachments

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import media

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)

# Initialize media
media.setup(db, messaging)
```

## Usage

### File Upload

```python
from src.core import media

# Upload a file
with open("image.jpg", "rb") as f:
    result = media.upload_file(
        user_id=1,
        file_data=f.read(),
        filename="image.jpg"
    )

print(f"File ID: {result.file_id}")
print(f"URL: {result.url}")
print(f"Thumbnails: {result.thumbnails}")

# Upload for messaging attachment
attachment = media.upload_attachment(
    user_id=1,
    file_data=image_bytes,
    filename="photo.png"
)
# Returns AttachmentData compatible with messaging.send_message()
```

### Stream Upload

```python
# Upload from stream (for large files)
result = media.upload_stream(
    user_id=1,
    stream=file_stream,
    filename="video.mp4",
    content_type="video/mp4",
    size=file_size
)
```

### File Retrieval

```python
# Get file metadata
file = media.get_file(file_id)
print(f"Filename: {file.original_filename}")
print(f"Size: {file.size}")
print(f"Type: {file.media_type}")

# Get file data
data, content_type = media.get_file_data(file_id)
```

### Thumbnails

```python
# Get all thumbnails for a file
thumbnails = media.get_thumbnails(file_id)
# {64: "/media/thumbnails/123/64.jpg", 128: "...", 256: "...", 512: "..."}

# Create thumbnail at specific size
url = media.create_thumbnail(file_id, size=200)
```

### Image Processing

```python
# Resize image
resized = media.resize_image(file_id, width=800)
resized = media.resize_image(file_id, height=600)
resized = media.resize_image(file_id, width=800, height=600)

# Convert format
webp_data = media.convert_image(file_id, "WEBP")
jpeg_data = media.convert_image(file_id, "JPEG")
```

### Video Metadata

```python
# Get video metadata
metadata = media.get_video_metadata(file_id)
if metadata:
    print(f"Duration: {metadata.duration}s")
    print(f"Resolution: {metadata.width}x{metadata.height}")
    print(f"Codec: {metadata.codec}")
```

### URL Signing

```python
# Generate signed URL with expiration
signed = media.sign_url(file_id, expires_in=3600)
print(f"Signed URL: {signed.url}")
print(f"Expires: {signed.expires_at}")

# Verify signed URL
is_valid, file_id = media.verify_signed_url(signed_url)
```

### External URL Proxy

```python
# Proxy external image
proxied = media.proxy_url("https://example.com/image.png")
print(f"Cached at: {proxied.storage_path}")

# Get proxied content
data, content_type = media.get_proxied_content("https://example.com/image.png")
```

### Malware Scanning

```python
# Scan file for malware
status, threat = media.scan_file(file_id)
if status == media.ScanStatus.INFECTED:
    print(f"Malware detected: {threat}")
elif status == media.ScanStatus.CLEAN:
    print("File is clean")
```

## Configuration

Add to `config/config.yaml`:

```yaml
media:
  # Storage backend: "local" or "s3"
  storage_backend: local
  
  # Local storage settings
  local_path: uploads
  local_url: /media
  
  # S3 storage settings
  s3_bucket: my-bucket
  s3_access_key: AKIAIOSFODNN7EXAMPLE
  s3_secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
  s3_region: us-east-1
  s3_endpoint: ""  # Custom endpoint for MinIO, etc.
  s3_public_url: ""  # Public URL prefix
  
  # File size limits (bytes)
  size_limits:
    image: 10485760      # 10MB
    video: 104857600     # 100MB
    audio: 52428800      # 50MB
    document: 26214400   # 25MB
    other: 10485760      # 10MB
  
  # Allowed content types
  allowed_types:
    image:
      - image/jpeg
      - image/png
      - image/gif
      - image/webp
    video:
      - video/mp4
      - video/webm
      - video/quicktime
    audio:
      - audio/mpeg
      - audio/ogg
      - audio/wav
    document:
      - application/pdf
      - text/plain
  
  # Thumbnail sizes to generate
  thumbnail_sizes: [64, 128, 256, 512]
  
  # Image processing
  image_quality: 85
  image_optimize: true
  
  # URL signing
  signing_key: your-secret-key-here
  signing_expiry: 3600  # seconds
  
  # Malware scanner (ClamAV)
  scanner_enabled: false
  scanner_host: localhost
  scanner_port: 3310
  
  # External URL proxy
  proxy_enabled: true
  proxy_cache_ttl: 86400  # seconds
  proxy_max_size: 10485760  # 10MB
```

## Storage Backends

### Local Filesystem

Files are stored in the configured `local_path` directory with the following structure:

```
uploads/
  image/
    2025/01/15/
      abc123def456.jpg
  video/
    2025/01/15/
      xyz789ghi012.mp4
  thumbnails/
    {file_id}/
      64.jpg
      128.jpg
      256.jpg
      512.jpg
```

### S3-Compatible Storage

Works with AWS S3, MinIO, DigitalOcean Spaces, and other S3-compatible services.

```python
# AWS S3
media:
  storage_backend: s3
  s3_bucket: my-bucket
  s3_access_key: AKIAIOSFODNN7EXAMPLE
  s3_secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
  s3_region: us-east-1

# MinIO
media:
  storage_backend: s3
  s3_bucket: my-bucket
  s3_access_key: minioadmin
  s3_secret_key: minioadmin
  s3_endpoint: http://localhost:9000
  s3_public_url: http://localhost:9000/my-bucket
```

## Thumbnail Sizes

| Size | Use Case |
|------|----------|
| 64px | Tiny icons, avatars |
| 128px | Small thumbnails |
| 256px | Medium thumbnails |
| 512px | Large thumbnails, previews |

## Media Types

| Type | Description |
|------|-------------|
| image | JPEG, PNG, GIF, WebP |
| video | MP4, WebM, QuickTime |
| audio | MP3, OGG, WAV |
| document | PDF, plain text |
| other | All other files |

## Error Handling

All media errors inherit from `MediaError`:

```python
from src.core.media import (
    MediaError,
    FileUploadError,
    FileSizeError,
    FileTypeError,
    StorageError,
    ImageProcessingError,
    VideoProcessingError,
    SigningError,
    SignatureExpiredError,
    SignatureInvalidError,
    ProxyError,
    ScannerError,
    MalwareDetectedError,
    PermissionDeniedError,
)

try:
    media.upload_file(user_id, data, filename)
except FileSizeError as e:
    print(f"File too large: {e.actual_size}/{e.max_size}")
except FileTypeError as e:
    print(f"Type not allowed: {e.content_type}")
    print(f"Allowed: {e.allowed_types}")
except MalwareDetectedError as e:
    print(f"Malware detected: {e.threat_name}")
```

## Dependencies

Required:
- PyYAML (configuration)

Optional:
- Pillow (image processing)
- boto3 (S3 storage)
- requests (external proxy)
- ffprobe (video metadata)

Install all:
```bash
pip install Pillow boto3 requests
```

## Database Schema

Tables (prefixed with `media_`):
- `media_files` - File metadata and storage info
- `media_thumbnails` - Generated thumbnails
- `media_proxy_cache` - Cached proxied content

## Testing

```bash
pytest src/tests/media/ -v
```

## Security Features

1. Content-type validation against whitelist
2. File size limits per media type
3. HMAC-SHA256 URL signing with expiration
4. Malware scanning via ClamAV
5. Path traversal prevention
6. Soft deletes with audit trail
