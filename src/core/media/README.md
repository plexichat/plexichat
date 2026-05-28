# Media Module

File upload, storage, and processing system for Plexichat API supporting
local filesystem, S3-compatible, and database storage backends.

## Module Architecture

The `media` module is split into small, focused sub-modules using a
**mixin** pattern.  The main `MediaManager` class inherits from all mixins
so the public API remains unchanged.

```
media/
  __init__.py          # Public API (setup + module-level functions)
  manager.py           # MediaManager class (~100 lines — just __init__ + MRO)
  _config.py           # DEFAULT_SIZE_LIMITS / ALLOWED_TYPES / THUMBNAIL_SIZES
  _validation.py       # _ValidationMixin  — filename sanitization, content-type detection,
  _storage_setup.py    # _StorageSetupMixin — storage backends, path generation, row→model
  _rate_limit.py       # _RateLimitMixin    — upload + thumbnail rate limiting
  _upload.py           # _UploadMixin       — upload_file / upload_stream / _do_upload
  _thumbnails.py       # _ThumbnailsMixin   — sync + background thumbnails
  _phash.py            # _PhashMixin        — background pHash similarity
  _files.py            # _FilesMixin        — get_file / delete_file / check_file_access
  _processing.py       # _ProcessingMixin   — resize / convert image, video metadata
  _signing.py          # _SigningMixin      — sign_url / verify_signed_url
  _proxy.py            # _ProxyMixin        — external URL proxy
  _scanning.py         # _ScanningMixin     — malware scanning
  models.py            # Data models / enums
  exceptions.py        # Exception hierarchy
  schema.py            # Database schema
  deduplication.py     # DeduplicationManager (content hashing)
  compression.py       # CompressionManager
  chunked.py           # ChunkedUploadManager (resumable uploads)
  phash.py             # Perceptual hash utilities
  processing/          # Image / video processors
  security/            # URL signing, malware scanner, proxy, validation
  storage/             # Local / S3 / Database storage backends
```

## Performance Optimisations

The attachment upload endpoint (`POST /api/v1/channels/{id}/attachments`)
has been heavily optimised:

| Technique | What It Does | Saving |
|-----------|-------------|--------|
| **In-memory I/O** | Files ≤8 MiB read directly to `bytes`, no temp file | ~40 ms disk I/O |
| **Deferred thumbnails** | Thumbnails generated fire-and-forget after response | ~100–200 ms |
| **Parallel malware scan** | ClamAV scan runs concurrent with compression+metadata | ~30 ms (overlap) |
| **Fast inline dedup** | 3 exact-hash queries only; O(n) pHash scan is background-only | ~20 ms |
| **Fire-and-forget rate limit** | Counter updates don't block the response | ~5 ms |

## Features

- File upload with configurable storage backends (local filesystem, S3-compatible)
- Image processing with Pillow (thumbnails, resizing, format conversion)
- Video metadata extraction using ffprobe
- Secure URL signing with HMAC and expiration
- External URL proxy with caching and SSRF protection
- Content-type validation and configurable file size limits
- Magic byte validation to prevent MIME type spoofing
- Malware scanning interface for ClamAV
- Integration with messaging module for attachments

## Security Features

The media module includes several security protections:

- **Magic byte validation**: Verifies file content matches declared MIME type
- **SSRF protection**: External proxy blocks internal/private IP addresses
- **Decompression bomb protection**: Limits max image dimensions and pixel count
- **ffprobe timeout**: Prevents hanging on malformed video files
- **Rate limiting**: Configurable limits for uploads and thumbnail generation
- **Path traversal prevention**: Storage backends validate paths
- **Content deduplication**: SHA-256 + perceptual-hash similarity detection

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
# Note: thumbnails are generated asynchronously in background
# they will be available in subsequent message/attachment responses

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

All media settings are configured in `config/config.yaml` under the `media` section.
The server loads these on startup with sensible defaults.

### Quick Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `storage_backend` | `local` | Primary storage: `local`, `s3`, or `database` |
| `stream_processing_max_bytes` | `8388608` | Max bytes to process in memory (8 MiB) |
| `database_max_size` | `524288` | Max file size for DB storage (512 KiB) |
| `auto_route_to_database.enabled` | `false` | Auto-route small text files to DB |
| `rate_limit.enabled` | `true` | Enable upload rate limiting |
| `rate_limit.uploads_per_minute` | `10` | Max uploads per minute per user |
| `rate_limit.max_total_size_per_day` | `536870912` | Max bytes per user per day (512 MiB) |

### Full Configuration Example

```yaml
media:
  # Primary storage backend: "local", "s3", or "database"
  storage_backend: local
  
  # Local storage settings
  local_path: ~/.plexichat/media
  local_url: /media
  
  # S3/MinIO storage settings
  s3_bucket: my-bucket
  s3_access_key: AKIAIOSFODNN7EXAMPLE
  s3_secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
  s3_region: us-east-1
  s3_endpoint: ""           # Custom endpoint for MinIO
  s3_public_url: ""         # Public URL prefix
  
  # In-memory processing (files ≤ this size skip temp-file disk I/O)
  stream_processing_max_bytes: 8388608  # 8 MiB
  
  # Database BLOB storage settings
  database_url: /api/v1/media/blob
  database_max_size: 524288  # 512 KiB max
  
  # Auto-routing: route small text files to database automatically
  auto_route_to_database:
    enabled: false
    max_size: 524288
    content_types:
      - text/plain
      - application/json
      - text/markdown
      - text/csv
  
  # File size limits per media type (bytes)
  size_limits:
    image: 10485760          # 10 MiB
    video: 104857600         # 100 MiB
    audio: 52428800          # 50 MiB
    document: 26214400       # 25 MiB
    other: 10485760          # 10 MiB
  
  # Allowed content types per media type
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
      - audio/webm
    document:
      - application/pdf
      - text/plain
      - application/zip
      - text/markdown
      - application/json
  
  # Thumbnail generation
  thumbnail_sizes: [64, 128, 256, 512]
  image_quality: 85
  image_optimize: true
  
  # URL signing for secure access
  signing_key: CHANGE_THIS_SIGNING_KEY
  signing_expiry: 3600       # 1 hour
  
  # Malware scanner (ClamAV)
  scanner_enabled: false
  scanner_host: localhost
  scanner_port: 3310
  
  # External URL proxy
  proxy_enabled: true
  proxy_cache_ttl: 86400     # 24 hours
  proxy_max_size: 10485760   # 10 MiB
  
  # Rate limiting
  rate_limit:
    enabled: true
    uploads_per_minute: 10
    uploads_per_hour: 100
    max_total_size_per_day: 536870912  # 512 MiB
```

### Rate Limiting

Rate limiting prevents abuse by restricting uploads per user:

- **Per minute**: Max uploads in a 60-second window
- **Per hour**: Max uploads in a 3600-second window  
- **Daily size**: Max total bytes uploaded in 24 hours

Check a user's rate limit status:

```python
status = media.get_rate_limit_status(user_id)
print(f"Uploads remaining this minute: {status['minute']['remaining']}")
print(f"Bytes remaining today: {status['day']['remaining_bytes']}")
```

To disable rate limiting:

```yaml
media:
  rate_limit:
    enabled: false
```

### Changing Size Limits

To allow larger video uploads (e.g., 500 MiB):

```yaml
media:
  size_limits:
    video: 524288000  # 500 MiB in bytes
```

### Production Checklist

1. **Change signing key**: Replace `CHANGE_THIS_SIGNING_KEY` with a secure random string
2. **Set up encryption keys**: Configure `PLEXICHAT_MEDIA_KEY` environment variable
3. **Configure storage**: Set up S3/MinIO for scalable storage
4. **Review size limits**: Adjust based on your use case
5. **Enable rate limiting**: Protect against abuse
6. **Consider malware scanning**: Enable ClamAV for user uploads

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
