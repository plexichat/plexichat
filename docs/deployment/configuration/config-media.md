# Media configuration

This guide describes how to configure media handling for Plexichat. Media settings control file uploads, storage backends, image processing, and malware scanning. All defaults in this guide come from `src/config_defaults.py:184`.

## Before you begin

You configure media under the `media` key in `config.yaml`.

```yaml
media:
  storage_backend: "local"
  local_path: "~/.plexichat/media"
  local_url: "/media"
```

Defaults use `~/.plexichat` as the home directory. See `src/config_defaults.py:62` for the `home_dir` definition.

## Choose a storage backend

```yaml
media:
  storage_backend: "local"  # "local", "s3", or "database"
```

| Backend | When to use it | Key trade-offs |
|---|---|---|
| `local` | Development, testing, single-server deployments with fewer than 1,000 users, or when you prioritize operational simplicity | No extra infrastructure, but no horizontal scaling and no built-in redundancy |
| `s3` | Any production or multi-server deployment, high-availability requirements, or large storage needs | Scales and supports CDN integration, but requires credentials and network connectivity |
| `database` | Very small deployments with fewer than 100 users where external storage is unavailable | Backs up with the database, but increases database size and performs poorly for large files |

S3-compatible options include AWS S3, Google Cloud Storage, Azure Blob Storage, RustFS, Wasabi, and Backblaze B2.

See the [Deployment guide](../index.md#backup-and-recovery) for backup strategies for each backend.

## Configure local storage

Local storage writes files to the filesystem. Use it for development or single-server deployments.

```yaml
media:
  storage_backend: "local"
  local_path: "~/.plexichat/media"
  local_url: "/media"
```

| Field | Default | Description |
|---|---|---|
| `local_path` | `~/.plexichat/media` | Filesystem path where the server stores media. The value comes from `home_dir / "media"` in `src/config_defaults.py:191`. |
| `local_url` | `/media` | URL prefix where the server serves media. |

Do not use the stale nested keys `media.local.path` or `media.local.url_prefix`. The correct flat keys are `media.local_path` and `media.local_url` as defined in `src/config_defaults.py:191`.

For production, use an absolute path such as `/var/lib/plexichat/media` and ensure the application user has read and write permissions. Place the directory on a filesystem with sufficient space and I/O performance, preferably SSD, and monitor disk usage. Configure your reverse proxy to serve `local_url` with caching headers. Back up the directory separately from the database.

Additional directory keys are available under `media` :

```yaml
media:
  data_dir: "~/.plexichat/data"
  logs_dir: "~/.plexichat/logs"
  media_dir: "~/.plexichat/media"
  temp_dir: "~/.plexichat/temp"
  encrypt_at_rest: true
```

`encrypt_at_rest` is `true` by default.

## Configure S3-compatible storage

S3 storage is the recommended backend for production.

```yaml
media:
  storage_backend: "s3"
  s3_bucket: "${S3_BUCKET:-}"
  s3_access_key: "${S3_ACCESS_KEY:-}"
  s3_secret_key: "${S3_SECRET_KEY:-}"
  s3_region: "${S3_REGION:-us-east-1}"
  s3_endpoint: "${S3_ENDPOINT:-}"
  s3_public_url: "${S3_PUBLIC_URL:-}"
```

Do not nest these keys under `media.s3`. The correct keys are flat under `media` with the `s3_` prefix, as defined in `src/config_defaults.py:193`.

| Field | Default | Description |
|---|---|---|
| `s3_bucket` | `""` | Bucket name. Use a globally unique, descriptive name. |
| `s3_access_key` | `""` | Access key ID. Use environment variables or a secrets manager. |
| `s3_secret_key` | `""` | Secret access key. Never commit it to version control. |
| `s3_region` | `us-east-1` | Bucket region. Choose the region closest to your users. |
| `s3_endpoint` | `""` | Custom endpoint URL. Leave empty for AWS S3. Set to your RustFS or Wasabi URL for other providers. |
| `s3_public_url` | `""` | Public URL or CDN URL for serving files. Leave empty to use the bucket URL. |

For AWS deployments, consider IAM roles instead of access keys. If you serve media through a CDN, set `s3_public_url` to your CloudFront distribution or custom domain.

Example bucket policy for public read access:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::your-bucket-name/*"
    }
  ]
}
```

Enable server-side encryption, configure CORS if you serve directly to browsers, and set lifecycle rules to move old files to cheaper storage tiers.

## Configure database storage

Database storage keeps small files in the database and serves them from the API.

```yaml
media:
  database_url: "/api/v1/media/blob"
  database_max_size: 524288  # 512 KB
  auto_route_to_database:
    enabled: true
    max_size: 524288
    content_types:
      - "text/plain"
      - "application/json"
      - "text/markdown"
      - "text/csv"
```

Files larger than `database_max_size` or with a content type not listed in `auto_route_to_database.content_types` use the configured `storage_backend` instead.

## Configure file size limits

Set per-type size limits under `media.size_limits`. There is no single `media.max_file_size` key.

```yaml
media:
  size_limits:
    image: 10485760      # 10 MB
    video: 104857600     # 100 MB
    audio: 52428800      # 50 MB
    document: 26214400   # 25 MB
    icon: 2097152        # 2 MB
    avatar: 5242880      # 5 MB
    other: 10485760      # 10 MB
```

| Category | Default | Typical adjustment |
|---|---|---|
| `image` | `10485760` (10 MB) | Increase for photography communities, decrease for bandwidth-constrained servers |
| `video` | `104857600` (100 MB) | Videos use the most bandwidth. Consider CDN integration before increasing. |
| `audio` | `52428800` (50 MB) |  |
| `document` | `26214400` (25 MB) |  |
| `icon` | `2097152` (2 MB) |  |
| `avatar` | `5242880` (5 MB) |  |
| `other` | `10485760` (10 MB) | Fallback for uncategorized types |

There is no `media.max_total_size_per_user` key in `src/config_defaults.py`. Enforce per-user quotas at the application or reverse-proxy layer if you need them.

Monitor storage cost with this formula: `users * avg_storage_per_user * cost_per_gb = monthly_cost`. For example, S3 Standard is about $0.023 per GB per month.

## Configure allowed file types

Restrict uploads by MIME type. Category keys are singular, not plural.

```yaml
media:
  allowed_types:
    image: ["image/jpeg", "image/png", "image/gif", "image/webp"]
    video: ["video/mp4", "video/webm", "video/quicktime"]
    audio: ["audio/mpeg", "audio/ogg", "audio/wav", "audio/webm"]
    document: ["application/pdf", "text/plain", "application/zip", "text/markdown", "application/json"]
```

Do not use plural keys such as `images` or `videos`. The correct keys are `image`, `video`, `audio`, and `document` as defined in `src/config_defaults.py:220`.

Validate MIME types on upload by inspecting file contents, not just extensions. If you allow documents or archives, enable malware scanning. Keep image processing libraries patched against exploits such as ImageTragick.

## Configure image processing

The server resizes images and generates thumbnails automatically.

```yaml
media:
  thumbnail_sizes: [64, 128, 256, 512]
  image_quality: 85
  image_optimize: true
  image_processing:
    max_dimension: 16384
    max_pixels: 178956970
    max_thumbnail_requests_per_minute: 60
  video_processing:
    ffprobe_timeout: 30
    max_size_for_metadata: 524288000
```

| Field | Default | Description |
|---|---|---|
| `thumbnail_sizes` | `[64, 128, 256, 512]` | Thumbnail edge lengths in pixels. Defined at `src/config_defaults.py:232`. |
| `image_quality` | `85` | JPEG/WebP quality from `1` to `100`. |
| `image_optimize` | `true` | When `true`, the server optimizes images on upload. |
| `image_processing.max_dimension` | `16384` | Maximum width or height in pixels before the server rejects the image. |
| `image_processing.max_pixels` | `178956970` | Maximum total pixels before the server rejects the image. |
| `image_processing.max_thumbnail_requests_per_minute` | `60` | Rate limit for thumbnail generation per user. |
| `video_processing.ffprobe_timeout` | `30` | Timeout in seconds for `ffprobe` metadata extraction. |
| `video_processing.max_size_for_metadata` | `524288000` (500 MB) | Maximum file size for which the server extracts video metadata. |

Do not use the stale keys `media.processing`, `media.processing.resize_images`, `media.processing.max_image_width`, `media.processing.max_image_height`, `media.processing.compress_videos`, or `media.processing.thumbnail_size`. The correct structure is `media.image_processing` and the top-level `media.thumbnail_sizes` list as defined in `src/config_defaults.py:232`.

Image processing is CPU-intensive. Monitor processing time and consider background workers if uploads block.

## Configure malware scanning

The server can scan uploads with ClamAV over TCP.

```yaml
media:
  scanner_enabled: false
  scanner_host: "localhost"
  scanner_port: 3310
```

| Field | Default | Description |
|---|---|---|
| `scanner_enabled` | `false` | When `true`, the server scans uploads with ClamAV. |
| `scanner_host` | `localhost` | Hostname of the ClamAV daemon. |
| `scanner_port` | `3310` | TCP port of the ClamAV daemon. |

Do not use the stale keys `media.malware_scanning.enabled` or `media.malware_scanning.clamav_socket`. The correct keys are `media.scanner_enabled`, `media.scanner_host`, and `media.scanner_port` as defined in `src/config_defaults.py:246`. The server connects over TCP to `localhost:3310`, not a Unix socket.

For Docker deployments, set the environment variables `SCANNER_ENABLED`, `SCANNER_HOST`, and `SCANNER_PORT`. The `docker-compose` stack includes a `clamav` service with a 4 GB RAM limit, automatic signature updates, and third-party signatures through the `clamav-unofficial-sigs` sidecar.

To install ClamAV natively:

1.  Install the packages for your distribution. For example, on Ubuntu or Debian, run `sudo apt-get install clamav clamav-daemon`.
2.  Configure `clamd` to listen on TCP port `3310`.
3.  Start the daemon with `sudo systemctl start clamav-daemon`.
4.  Update definitions with `sudo freshclam`.

Scanning adds 1–5 seconds per file. Monitor ClamAV health and test with the EICAR test file.

## Configure media rate limiting

Rate limiting for media controls upload volume per user.

```yaml
media:
  rate_limit:
    enabled: true
    uploads_per_minute: 10
    uploads_per_hour: 100
    max_total_size_per_day: 536870912  # 512 MB
```

| Field | Default | Description |
|---|---|---|
| `rate_limit.enabled` | `true` | When `true`, the server enforces media rate limits. |
| `rate_limit.uploads_per_minute` | `10` | Maximum uploads per minute per user. |
| `rate_limit.uploads_per_hour` | `100` | Maximum uploads per hour per user. |
| `rate_limit.max_total_size_per_day` | `536870912` (512 MB) | Maximum total bytes a user can upload per day. |

Thumbnail generation has its own limit at `media.image_processing.max_thumbnail_requests_per_minute` (`60` per minute). Do not use the stale key `media.rate_limit.thumbnails_per_minute`. See `src/config_defaults.py:235` for the correct structure.

## Configure the external proxy

The server can proxy external URLs to hide user IP addresses and apply caching.

```yaml
media:
  proxy_enabled: true
  proxy_cache_ttl: 86400
  proxy_max_size: 10485760
  proxy_buffer_size: 65536
```

| Field | Default | Description |
|---|---|---|
| `proxy_enabled` | `true` | When `true`, the server proxies external URL fetches. |
| `proxy_cache_ttl` | `86400` (24 hours) | Cache lifetime for proxied responses. |
| `proxy_max_size` | `10485760` (10 MB) | Maximum size of a proxied response. |
| `proxy_buffer_size` | `65536` (64 KB) | Buffer size for proxy streaming. |

Do not use the stale keys `media.external_proxy.enabled` or `media.external_proxy.timeout_seconds`. The correct keys are `media.proxy_enabled`, `media.proxy_cache_ttl`, `media.proxy_max_size`, and `media.proxy_buffer_size` as defined in `src/config_defaults.py:249`.

## Related documentation

*   [Default configuration reference](../../default-config.md) — Complete configuration reference.
*   [Deployment guide](../getting-started.md) — Deployment and CDN integration.
*   [Security best practices](../../security.md) — File upload security.
*   [Voice configuration](config-voice.md) — Voice and video file handling.
