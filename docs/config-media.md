# Media Configuration

This guide covers media configuration for deploying Plexichat in production. Media settings control file uploads, storage backends, image processing, and security features like malware scanning. Proper configuration is essential for managing storage costs, ensuring security, and providing good user experience.

## Configuration Location

All media settings are nested under the `media` key in your configuration file:

```yaml
media:
  # All media settings go here
```

## Storage Backend Selection

Choose between local filesystem, S3-compatible storage, or database storage for uploaded files.

### Configuration

```yaml
media:
  storage_backend: "local"
```

### Deployment Considerations

**Why Storage Choice Matters**

The storage backend determines scalability, cost structure, performance characteristics, and backup requirements. This is a critical infrastructure decision that affects operational complexity and total cost of ownership. See [Deployment Guide](deployment.md#backup-and-recovery) for backup strategies.

**Local Storage**

**When to Use**

- Development and testing environments
- Small deployments with fewer than 1,000 users
- Single-server deployments without horizontal scaling
- Scenarios where operational simplicity is prioritized

**Advantages**

- Zero additional infrastructure requirements
- Simple configuration and maintenance
- No ongoing costs (beyond disk space)
- Fast local file access

**Limitations**

- Cannot scale horizontally (files are tied to specific server)
- Disk space is finite and requires monitoring
- No built-in redundancy or high availability
- Backup requires separate strategy

**Operational Notes**

- Place media directory on a filesystem with sufficient space and I/O performance
- Monitor disk usage and implement cleanup policies for old files
- Consider using SSDs for better performance
- Implement regular backups of the media directory

**S3-Compatible Storage**

**When to Use**

- Production deployments of any scale
- Multi-server deployments requiring shared storage
- High-availability requirements
- Deployments with large media storage needs

**Advantages**

- Virtually unlimited storage scalability
- Built-in redundancy and high availability
- Geographic distribution options
- Cost-effective for large datasets with infrequent access tiers
- CDN integration for improved performance

**Limitations**

- Additional infrastructure and cost
- Requires network connectivity (latency vs local storage)
- Vendor lock-in considerations
- Requires API credentials management

**S3-Compatible Services**

- AWS S3
- Google Cloud Storage
- Azure Blob Storage
- MinIO (self-hosted S3-compatible)
- Wasabi, Backblaze B2, and other S3-compatible providers

**Database Storage**

**When to Use**

- Very small deployments (<100 users)
- Deployments where external storage is not available
- Scenarios requiring transactional consistency with database

**Advantages**

- Single backup strategy (database backup covers media)
- Transactional consistency
- No additional infrastructure

**Limitations**

- Significantly increases database size
- Poor performance for large files
- Database backup/restore slower
- Not recommended for production

---

## Local Storage Configuration

Settings for filesystem-based media storage.

### Configuration

```yaml
media:
  storage_backend: "local"
  local:
    path: "data/media"
    url_prefix: "/media"
```

### Deployment Considerations

**File Path**

- **Default**: `data/media` relative to application directory
- **Production Recommendation**: Use an absolute path to a dedicated data directory (e.g., `/var/lib/plexichat/media`)
- **Permissions**: Ensure the application user has read/write permissions to the directory
- **Filesystem**: Place on a filesystem with good I/O performance (SSD preferred)

**URL Prefix**

- **Default**: `/media` is the URL path for serving files
- **Custom**: Change if you need a different path (e.g., `/files`, `/uploads`)
- **Reverse Proxy**: Configure your reverse proxy (nginx, Apache) to serve this path
- **CDN**: Point this to a CDN URL if using CDN for media delivery

**Operational Notes**

- Ensure sufficient disk space for expected media storage
- Monitor disk usage and implement cleanup policies
- Consider separating media onto its own disk or mount point
- Implement regular backups of the media directory
- Configure reverse proxy to serve static files efficiently (with caching headers)

---

## S3 Configuration

Settings for S3-compatible object storage.

### Configuration

```yaml
media:
  storage_backend: "s3"
  s3:
    bucket: ""
    region: "us-east-1"
    access_key_id: ""
    secret_access_key: ""
    endpoint_url: ""
    public_url: ""
```

### Deployment Considerations

**Bucket Configuration**

- **Bucket Name**: Must be globally unique across S3. Use a descriptive name.
- **Region**: Choose region closest to your users for best performance
- **Public Access**: Configure bucket policy to allow public read access for media files
- **Versioning**: Consider enabling for recovery from accidental deletions
- **Lifecycle Rules**: Configure to move old files to cheaper storage tiers

**Access Credentials**

- **Access Key ID**: AWS access key or equivalent for S3-compatible service
- **Secret Access Key**: AWS secret key or equivalent (keep secure)
- **Security**: Never commit credentials to version control. Use environment variables or secrets management
- **IAM Roles**: For AWS deployments, consider using IAM roles instead of access keys

**Endpoint URL**

- **AWS S3**: Leave empty to use default AWS endpoint
- **MinIO**: Specify your MinIO server URL (e.g., `https://minio.example.com`)
- **Other Providers**: Specify custom endpoint for S3-compatible services (Wasabi, Backblaze B2, etc.)

**Public URL**

- **Default**: Files are served from S3 bucket URL
- **CloudFront**: Specify CloudFront distribution URL for CDN delivery
- **Custom Domain**: Specify custom domain if using CNAME to S3 bucket
- **Benefits**: CDN reduces latency, reduces S3 costs, provides better performance

**S3 Bucket Policy Example**

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

**Operational Notes**

- Enable server-side encryption for sensitive data
- Configure CORS if serving directly to browsers
- Monitor S3 costs and implement lifecycle policies
- Use appropriate storage classes (Standard, Intelligent-Tiering, Glacier) based on access patterns
- Consider enabling S3 Transfer Acceleration for faster uploads

---

## File Limits

Configure maximum file sizes and storage quotas.

### Configuration

```yaml
media:
  max_file_size: 104857600
  max_total_size_per_user: 10737418240
```

### Deployment Considerations

**Maximum File Size**

- **Default**: 104857600 bytes (100MB)
- **Small Deployment**: 50MB (52428800) for bandwidth-constrained environments
- **Standard Deployment**: 100MB is appropriate for most applications
- **Large Deployment**: 500MB (524288000) for media-heavy applications
- **Unlimited**: Not recommended due to abuse potential and storage costs

**Rationale**: File size limits prevent abuse, manage storage costs, and ensure reasonable upload times. Too restrictive limits user experience. Too permissive increases costs and abuse risk.

**Maximum Total Size Per User**

- **Default**: 10737418240 bytes (10GB)
- **Small Deployment**: 1GB (1073741824) for limited storage
- **Standard Deployment**: 10GB is appropriate for most users
- **Large Deployment**: 50GB (53687091200) for media-heavy applications
- **Unlimited**: Not recommended due to unlimited cost exposure

**Rationale**: Per-user quotas ensure fair resource allocation and prevent storage exhaustion by individual users. Calculate based on:
- Expected average media usage per user
- Available storage capacity
- Cost considerations
- User expectations

**Cost Implications**

- **AWS S3 Standard**: ~$0.023/GB/month
- **Google Cloud Storage**: ~$0.020/GB/month
- **MinIO**: Cost of your storage hardware
- **Formula**: `users * avg_storage_per_user * cost_per_gb = monthly_cost`

Consider [Database Configuration](config-database.md) for database storage costs as part of total TCO.

**Operational Notes**

- Monitor storage usage per user and overall
- Implement cleanup policies for old or unused files
- Consider tiered storage based on user tiers (paid vs free)
- Provide users with visibility into their storage usage
- Implement storage overage policies or upgrade paths

---

## Allowed File Types

Configure which MIME types are accepted for uploads.

### Configuration

```yaml
media:
  allowed_types:
    images: ["image/jpeg", "image/png", "image/gif", "image/webp"]
    videos: ["video/mp4", "video/webm"]
    audio: ["audio/mpeg", "audio/ogg", "audio/wav"]
    documents: ["application/pdf", "text/plain"]
```

### Deployment Considerations

**Why Type Restrictions Matter**

Restricting file types prevents abuse, reduces security risks, and ensures compatibility with media processing pipelines.

**Images**

- **Default**: JPEG, PNG, GIF, WebP cover most use cases
- **Additions**: Consider adding SVG for vector graphics (security implications)
- **Removals**: Remove formats with poor browser support
- **Security**: Image formats can contain exploits (ImageTragick). Keep updated with security patches.

**Videos**

- **Default**: MP4, WebP cover most modern browsers
- **Additions**: Consider adding MOV for Apple devices
- **Processing**: Video processing is resource-intensive. Monitor server load.
- **Bandwidth**: Videos consume significant bandwidth. Consider CDN integration.

**Audio**

- **Default**: MP3, OGG, WAV cover most use cases
- **Additions**: Consider adding FLAC for lossless audio
- **Processing**: Audio processing is less intensive than video
- **Bandwidth**: Audio files are typically smaller than videos

**Documents**

- **Default**: PDF, plain text are safe choices
- **Additions**: Consider adding DOCX, XLSX with caution (security risks)
- **Security**: Office documents can contain macros. Use malware scanning.
- **Processing**: Document preview may require additional libraries.

**Security Considerations**

- Validate MIME types on upload (not just file extension)
- Use magic number detection to identify actual file types
- Scan uploaded files for malware if enabling document types (see Malware Scanning section below)
- Consider sandboxing file processing operations
- See [Security Best Practices](security.md) for general security guidance

**Operational Notes**

- Review allowed types periodically for security updates
- Monitor upload failures due to type restrictions
- Provide clear error messages when file type is rejected
- Consider implementing file type conversion on the server

---

## Image Processing

Configure automatic image resizing and thumbnail generation.

### Configuration

```yaml
media:
  processing:
    resize_images: true
    max_image_width: 4096
    max_image_height: 4096
    thumbnail_size: 300
    compress_videos: false
```

### Deployment Considerations

**Why Image Processing Matters**

Automatic resizing ensures consistent image sizes, reduces storage costs, and improves loading performance. However, it adds CPU load and processing time.

**Resize Images**

- **Enable** for production to normalize image sizes and reduce storage
- **Disable** if you want to preserve original image quality
- **Performance**: Image processing is CPU-intensive. Monitor server load.
- **Quality**: Resizing may reduce image quality. Balance with user expectations.

**Maximum Image Dimensions**

- **Default**: 4096x4096 pixels (4K resolution)
- **Standard Deployment**: 4096 is appropriate for high-quality displays
- **Mobile-First**: Reduce to 2048 for mobile-focused applications
- **High-Quality**: Increase to 8192 for photography applications

**Rationale**: Maximum dimensions prevent storage of excessively large images while maintaining quality for most use cases. Larger images consume more storage and bandwidth.

**Thumbnail Size**

- **Default**: 300 pixels square
- **Standard Deployment**: 300 is appropriate for most UI elements
- **High-DPI**: Increase to 600 for retina displays
- **Low-Bandwidth**: Reduce to 150 for bandwidth-constrained environments

**Rationale**: Thumbnails provide fast-loading previews. Size depends on your UI design and bandwidth considerations.

**Video Compression**

- **Default**: Disabled due to high CPU usage
- **Enable** only if you have significant video upload volume
- **Requirements**: Requires FFmpeg installation and significant CPU resources
- **Trade-off**: Reduces storage and bandwidth but increases upload processing time

**Operational Notes**

- Image processing requires sufficient CPU resources
- Monitor processing queue length and processing time
- Consider using background workers for processing to avoid blocking uploads
- Test image processing with various file types and sizes
- Consider implementing progressive image loading for better UX

---

## Malware Scanning

Configure ClamAV integration for virus scanning.

### Configuration

```yaml
media:
  malware_scanning:
    enabled: false
    clamav_socket: "/var/run/clamav/clamd.ctl"
```

### Deployment Considerations

**Why Malware Scanning Matters**

Malware scanning protects your users and infrastructure from malicious file uploads. This is particularly important for document types and executables.

**When to Enable**

- **Required For**: Applications accepting documents, archives, or executables
- **Recommended For**: Public-facing applications with open registration
- **Optional For**: Applications with trusted user base or limited file types
- **Not Required For**: Image-only applications (images have lower malware risk)

**ClamAV Socket**

- **Default**: `/var/run/clamav/clamd.ctl` is the standard ClamAV socket path
- **TCP Socket**: Can also use TCP socket (e.g., `tcp://localhost:3310`)
- **Installation**: Requires ClamAV installation and configuration
- **Performance**: Scanning adds processing time to uploads

**Operational Requirements**

1. Install ClamAV:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install clamav clamav-daemon

   # CentOS/RHEL
   sudo yum install clamav clamav-server
   ```

2. Configure ClamAV daemon (clamd):
   - Edit `/etc/clamav/clamd.conf` or `/etc/clamd.d/scan.conf`
   - Set `LocalSocket /var/run/clamav/clamd.ctl`
   - Set `TCPSocket 3310` (if using TCP)
   - Enable `ScanOnAccess` if desired

3. Start ClamAV daemon:
   ```bash
   sudo systemctl start clamav-daemon
   sudo systemctl enable clamav-daemon
   ```

4. Update virus definitions:
   ```bash
   sudo freshclam
   ```

**Performance Impact**

- **Scanning Time**: Adds 1-5 seconds per file depending on file size
- **CPU Usage**: Moderate CPU usage during scanning
- **Throughput**: May limit upload rate if many concurrent uploads
- **Recommendation**: Use background processing for scanning to avoid blocking uploads

**Operational Notes**

- Schedule regular virus definition updates (freshclam)
- Monitor ClamAV daemon health and performance
- Test scanning with EICAR test file to verify functionality
- Implement quarantine for infected files
- Provide clear error messages when files are rejected

---

## Rate Limiting

Configure rate limits for media operations.

### Configuration

```yaml
media:
  rate_limit:
    uploads_per_minute: 10
    thumbnails_per_minute: 30
```

### Deployment Considerations

**Why Rate Limiting Matters**

Rate limiting prevents abuse, manages server load, and ensures fair resource allocation. Without limits, malicious users could exhaust resources through rapid uploads.

**Uploads Per Minute**

- **Default**: 10 uploads per minute per user
- **Standard Deployment**: 10 is appropriate for most applications
- **High-Volume**: Increase to 30 for media-heavy applications
- **Low-Volume**: Reduce to 5 for bandwidth-constrained environments
- **Abuse Prevention**: Lower limits reduce impact of abuse attempts

**Thumbnails Per Minute**

- **Default**: 30 thumbnail generations per minute per user
- **Standard Deployment**: 30 is appropriate (thumbnails are smaller/faster)
- **High-Volume**: Increase to 100 for applications with many images
- **Low-Volume**: Reduce to 10 for CPU-constrained environments

**Rationale**: Thumbnail generation is CPU-intensive. Rate limiting prevents CPU exhaustion from rapid thumbnail generation requests.

**Operational Notes**

- Monitor rate limit violations for abuse patterns
- Adjust limits based on server capacity and user behavior
- Consider implementing tiered limits based on user roles
- Provide clear error messages when rate limits are exceeded
- Monitor CPU usage during peak upload periods

---

## External Proxy

Configure proxying of external URLs through your server.

### Configuration

```yaml
media:
  external_proxy:
    enabled: false
    timeout_seconds: 10
```

### Deployment Considerations

**Why External Proxying Matters**

When users share external URLs (e.g., in messages), the server may need to fetch metadata or content. Proxying through your server prevents IP address leakage and provides security benefits.

**When to Enable**

- **Required For**: URL preview features (fetching OpenGraph metadata)
- **Recommended For**: Applications where user privacy is critical
- **Optional For**: Applications where IP disclosure is acceptable
- **Not Required For**: Applications without external URL features

**Security Benefits**

- **IP Protection**: Hides users' IP addresses from external servers
- **SSRF Prevention**: Prevents Server-Side Request Forgery attacks
- **Content Filtering**: Allows filtering of malicious external content
- **Rate Limiting**: Apply rate limits to external fetches

**Performance Considerations**

- **Latency**: Adds network latency for external URL fetches
- **Bandwidth**: Increases your server's bandwidth usage
- **Timeout**: Configure appropriate timeout to prevent hanging requests
- **Caching**: Implement caching for frequently accessed external content

**Operational Notes**

- Monitor proxy usage for abuse patterns
- Implement allowlist/denylist for external domains
- Configure appropriate timeout based on expected response times
- Consider implementing caching to reduce external requests
- Monitor bandwidth usage from proxy traffic

---

## CDN Integration

For production deployments, consider integrating a CDN for media delivery.

### Configuration

CDN integration is typically configured through the `public_url` setting in S3 configuration or reverse proxy configuration for local storage.

### Deployment Considerations

**Why CDN Matters**

CDNs significantly improve performance for geographically distributed users by serving content from edge locations closer to users.

**CDN Options**

- **CloudFront**: AWS CDN, integrates well with S3
- **Cloudflare**: Global CDN with DDoS protection
- **Fastly**: High-performance CDN with edge computing
- **BunnyCDN**: Cost-effective CDN with good performance
- **Akamai**: Enterprise-grade CDN

**Configuration Steps**

1. **For S3 Storage**: Set `public_url` to CDN distribution URL
2. **For Local Storage**: Configure reverse proxy to cache media files
3. **Cache Headers**: Configure appropriate cache headers for media files
4. **Invalidation**: Implement cache invalidation for updated/deleted files

**Cache Headers**

- **Static Images**: Cache for 1 year (immutable content)
- **User Uploads**: Cache for 1 day to 1 week (may be deleted)
- **Thumbnails**: Cache for 1 day to 1 week
- **Example Header**: `Cache-Control: public, max-age=31536000, immutable`

**Operational Notes**

- Monitor CDN cache hit rates
- Implement cache invalidation when files are updated or deleted
- Consider using signed URLs for private content
- Monitor CDN costs and optimize cache strategies
- Test CDN delivery from multiple geographic locations

---

## Backup and Recovery

### Local Storage Backups

**Strategy**

- Include media directory in regular filesystem backups
- Use incremental backups to reduce backup size and time
- Consider snapshot-based backups for point-in-time recovery

**Procedure**

```bash
# Using rsync for incremental backup
rsync -av --progress /var/lib/plexichat/media/ /backup/media/

# Using tar for full backup
tar -czf /backup/media-$(date +%Y%m%d).tar.gz /var/lib/plexichat/media/
```

### S3 Backups

**Strategy**

- S3 provides built-in durability (99.999999999%)
- Enable versioning for recovery from accidental deletions
- Use Cross-Region Replication for disaster recovery
- Implement lifecycle policies for cost optimization

**Procedure**

S3 backups are automatic, but configure:

1. **Versioning**: Enable in bucket settings
2. **Lifecycle Rules**: Move old versions to Glacier after X days
3. **Cross-Region Replication**: Replicate to secondary region for DR
4. **Backup to Glacier**: Archive old files to Glacier for long-term retention

---

## Related Documentation

- [Default Configuration Reference](default-config.md) - Complete configuration reference
- [Deployment Guide](deployment.md) - Deployment and CDN integration
- [Security Best Practices](security.md) - File upload security
- [Voice Configuration](config-voice.md) - Voice/video file handling
