# Media Module

File upload, storage, and processing system for PlexiChat API supporting local filesystem and S3-compatible storage backends.

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

All media settings are configured in `config/config.yaml` under the `media` section. The server loads these on startup with sensible defaults.

### Quick Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `storage_backend` | `local` | Primary storage: `local`, `s3`, or `database` |
| `database_max_size` | `524288` | Max file size for DB storage (512KB) |
| `auto_route_to_database.enabled` | `false` | Auto-route small text files to DB |
| `rate_limit.enabled` | `true` | Enable upload rate limiting |
| `rate_limit.uploads_per_minute` | `10` | Max uploads per minute per user |
| `rate_limit.max_total_size_per_day` | `536870912` | Max bytes per user per day (512MB) |

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
  
  # Database BLOB storage settings
  database_url: /api/v1/media/blob
  database_max_size: 524288  # 512KB max
  
  # Auto-routing: route small text files to database automatically
  auto_route_to_database:
    enabled: false
    max_size: 524288         # Files under 512KB
    content_types:           # Only these types get routed
      - text/plain
      - application/json
      - text/markdown
      - text/csv
  
  # File size limits per media type (bytes)
  size_limits:
    image: 10485760          # 10MB
    video: 104857600         # 100MB
    audio: 52428800          # 50MB
    document: 26214400       # 25MB
    other: 10485760          # 10MB
  
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
  proxy_max_size: 10485760   # 10MB
  
  # Rate limiting
  rate_limit:
    enabled: true
    uploads_per_minute: 10
    uploads_per_hour: 100
    max_total_size_per_day: 536870912  # 512MB
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

### Auto-Routing to Database

When enabled, small text files (like long messages converted to .txt) are automatically stored in the database instead of the primary storage backend. This keeps simple text content in one place without needing external storage.

```yaml
media:
  storage_backend: s3        # Primary backend for images/videos
  auto_route_to_database:
    enabled: true
    max_size: 524288         # Route files under 512KB
    content_types:
      - text/plain           # .txt files
      - application/json     # .json files
      - text/markdown        # .md files
```

Files are automatically routed based on content type and size. The `storage_backend` field in the database tracks which backend each file uses.

### Changing Size Limits

To allow larger video uploads (e.g., 500MB):

```yaml
media:
  size_limits:
    video: 524288000  # 500MB in bytes
```

### Adding Allowed Content Types

To allow SVG images:

```yaml
media:
  allowed_types:
    image:
      - image/jpeg
      - image/png
      - image/gif
      - image/webp
      - image/svg+xml  # Added
```

### Production Checklist

1. **Change signing key**: Replace `CHANGE_THIS_SIGNING_KEY` with a secure random string
2. **Configure storage**: Set up S3/MinIO for scalable storage
3. **Review size limits**: Adjust based on your use case
4. **Enable rate limiting**: Protect against abuse
5. **Consider malware scanning**: Enable ClamAV for user uploads
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

```yaml
# AWS S3
media:
  storage_backend: s3
  s3_bucket: my-bucket
  s3_access_key: AKIAIOSFODNN7EXAMPLE
  s3_secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
  s3_region: us-east-1
```

### MinIO Setup (Recommended for Development/Self-Hosted)

MinIO is an S3-compatible object storage server that runs locally or on your own infrastructure. It's the easiest way to move away from directory-based file storage without needing AWS.

#### Why MinIO?

- S3-compatible API (no code changes needed)
- Runs locally for development, scales for production
- Web console for easy file management
- Free and open source
- Single binary, minimal setup

#### Installation

**Windows (Recommended: Standalone Binary)**
```powershell
# Download MinIO server
Invoke-WebRequest -Uri "https://dl.min.io/server/minio/release/windows-amd64/minio.exe" -OutFile "minio.exe"

# Create data directory
mkdir C:\minio-data
```

**Windows (Docker)**
```powershell
docker run -d --name minio `
  -p 9000:9000 -p 9001:9001 `
  -v C:\minio-data:/data `
  -e MINIO_ROOT_USER=minioadmin `
  -e MINIO_ROOT_PASSWORD=minioadmin `
  minio/minio server /data --console-address ":9001"
```

**Linux/macOS (Docker)**
```bash
docker run -d --name minio \
  -p 9000:9000 -p 9001:9001 \
  -v ~/minio-data:/data \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"
```

**Linux (Binary)**
```bash
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio
mkdir ~/minio-data
```

**macOS (Homebrew)**
```bash
brew install minio/stable/minio
mkdir ~/minio-data
```

#### Running MinIO

**Standalone Binary**
```powershell
# Windows
.\minio.exe server C:\minio-data --console-address ":9001"

# Linux/macOS
./minio server ~/minio-data --console-address ":9001"
```

**With Custom Credentials**
```powershell
# Windows PowerShell
$env:MINIO_ROOT_USER="plexichat"
$env:MINIO_ROOT_PASSWORD="your-secure-password-here"
.\minio.exe server C:\minio-data --console-address ":9001"

# Linux/macOS
MINIO_ROOT_USER=plexichat MINIO_ROOT_PASSWORD=your-secure-password-here ./minio server ~/minio-data --console-address ":9001"
```

#### Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| API | http://localhost:9000 | S3-compatible API endpoint |
| Console | http://localhost:9001 | Web UI for management |

Default credentials: `minioadmin` / `minioadmin`

#### Creating a Bucket

**Option 1: Web Console**
1. Open http://localhost:9001
2. Login with your credentials
3. Click "Create Bucket"
4. Name it `plexichat-media`
5. Set access policy to "Public" (for direct URL access) or keep "Private" (for signed URLs)

**Option 2: MinIO Client (mc)**
```bash
# Install mc (MinIO Client)
# Windows: Download from https://dl.min.io/client/mc/release/windows-amd64/mc.exe
# macOS: brew install minio/stable/mc
# Linux: wget https://dl.min.io/client/mc/release/linux-amd64/mc && chmod +x mc

# Configure alias
mc alias set local http://localhost:9000 minioadmin minioadmin

# Create bucket
mc mb local/plexichat-media

# Set public read policy (optional, for direct URL access)
mc anonymous set download local/plexichat-media
```

#### PlexiChat Configuration

Add to `config/config.yaml`:

```yaml
media:
  storage_backend: s3
  
  # MinIO connection
  s3_bucket: plexichat-media
  s3_access_key: minioadmin          # Or your custom MINIO_ROOT_USER
  s3_secret_key: minioadmin          # Or your custom MINIO_ROOT_PASSWORD
  s3_region: us-east-1               # Required but ignored by MinIO
  s3_endpoint: http://localhost:9000
  s3_public_url: http://localhost:9000/plexichat-media
  
  # Rest of media config...
  size_limits:
    image: 10485760
    video: 104857600
    audio: 52428800
    document: 26214400
    other: 10485760
```

#### Production Deployment

For production, consider:

1. **Use strong credentials**
   ```bash
   MINIO_ROOT_USER=<random-20-char-string>
   MINIO_ROOT_PASSWORD=<random-40-char-string>
   ```

2. **Enable TLS**
   ```bash
   minio server ~/minio-data --certs-dir ~/.minio/certs
   ```
   Place `public.crt` and `private.key` in the certs directory.

3. **Run as a service**
   
   Windows (NSSM):
   ```powershell
   nssm install MinIO "C:\path\to\minio.exe" "server C:\minio-data --console-address :9001"
   nssm set MinIO AppEnvironmentExtra MINIO_ROOT_USER=plexichat MINIO_ROOT_PASSWORD=your-password
   nssm start MinIO
   ```
   
   Linux (systemd):
   ```ini
   # /etc/systemd/system/minio.service
   [Unit]
   Description=MinIO
   After=network.target
   
   [Service]
   User=minio
   Group=minio
   Environment="MINIO_ROOT_USER=plexichat"
   Environment="MINIO_ROOT_PASSWORD=your-secure-password"
   ExecStart=/usr/local/bin/minio server /data --console-address ":9001"
   Restart=always
   
   [Install]
   WantedBy=multi-user.target
   ```

4. **Distributed mode** (multiple servers for redundancy)
   ```bash
   minio server http://server{1...4}/data
   ```

#### Troubleshooting

| Issue | Solution |
|-------|----------|
| Connection refused | Ensure MinIO is running and port 9000 is not blocked |
| Access denied | Check credentials match between MinIO and config.yaml |
| Bucket not found | Create the bucket via console or mc client |
| CORS errors | Configure bucket CORS policy in MinIO console |
| boto3 not found | Run `pip install boto3` |

#### Verifying Setup

```python
# Quick test script
from src.core.media.storage import S3Storage

storage = S3Storage(
    bucket="plexichat-media",
    access_key="minioadmin",
    secret_key="minioadmin",
    endpoint_url="http://localhost:9000",
    public_url="http://localhost:9000/plexichat-media",
)

# Test upload
storage.store(b"Hello MinIO!", "test.txt", "text/plain")
print(f"Uploaded: {storage.get_url('test.txt')}")

# Test retrieve
data = storage.retrieve("test.txt")
print(f"Retrieved: {data.decode()}")

# Cleanup
storage.delete("test.txt")
print("Test passed!")
```

### Database Storage (Small Files Only)

For very small files like text conversions of long messages, configuration snippets, or tiny documents, you can store files directly in the database as BLOBs. This eliminates external dependencies and keeps everything in one place.

#### When to Use Database Storage

| Good For | Not Good For |
|----------|--------------|
| Long messages converted to .txt (<500KB) | Images, videos, audio |
| Small config/data files | Files needing direct URL streaming |
| Tiny thumbnails or icons | Anything over 512KB |
| Single-server deployments | High-throughput file serving |

#### Configuration

```yaml
media:
  storage_backend: database
  database_url: /api/v1/media/blob    # API endpoint for serving
  database_max_size: 524288            # 512KB max (in bytes)
```

#### How It Works

1. Files are stored as BLOBs in the `media_blobs` table
2. Served via an API endpoint (not direct file URLs)
3. Automatic deduplication via SHA-256 checksums
4. Same interface as other storage backends

#### API Endpoint

Files stored in the database are served via `/api/v1/media/blob/{encoded_path}`. The path is base64-encoded in the URL.

#### Example Usage

```python
from src.core.media.storage import DatabaseStorage

# Initialize with database connection
storage = DatabaseStorage(
    db=db,
    base_url="/api/v1/media/blob",
    max_size=512 * 1024,  # 512KB
)

# Store a text file (e.g., long message converted to .txt)
message_text = "Very long message content..." * 1000
storage.store(message_text.encode(), "messages/12345.txt", "text/plain")

# Retrieve
data = storage.retrieve("messages/12345.txt")

# Check for duplicates by checksum
checksum = hashlib.sha256(data).hexdigest()
existing = storage.get_by_checksum(checksum)

# Get storage stats
total_size = storage.get_total_size()
file_count = storage.get_count()
```

#### Hybrid Approach

You can use database storage alongside other backends. The `storage_backend` in `media_files` table tracks which backend each file uses, so you could:

1. Use `database` for text files under 500KB
2. Use `s3` or `local` for larger media files

This requires custom logic in your upload handler to choose the backend based on file type/size.

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
