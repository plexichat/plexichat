# Media storage

Use the media storage package to store, retrieve, and delete uploaded files through a unified interface.

## Overview

All backends implement `StorageBackendBase` (`base.py:9`). You can switch backends without changing application code.

| Backend | File | Use case |
|---|---|---|
| `LocalStorage` | `local.py:19` | Filesystem storage with path-traversal protection |
| `S3Storage` | `s3.py:19` | AWS S3 or S3-compatible services (RustFS, SeaweedFS) |
| `DatabaseStorage` | `database.py` | Database-backed storage |
| `EncryptedStorage` | `encrypted.py` | Transparent encryption wrapper for any backend |

## The storage interface

`StorageBackendBase` (`base.py:9`) defines the following methods:

| Method | Description |
|---|---|
| `store(file_data, path, content_type)` | Store bytes at `path` |
| `store_stream(stream, path, content_type, size)` | Store from a file-like object |
| `retrieve(path)` | Retrieve bytes |
| `retrieve_stream(path)` | Retrieve as `(stream, size)` |
| `delete(path)` | Delete the file |
| `exists(path)` | Check whether the file exists |
| `get_url(path)` | Get a public or presigned URL |
| `get_size(path)` | Get file size in bytes |
| `is_encrypted(path)` | Check whether the file is encrypted |
| `get_metadata(path)` | Get metadata dict |

All methods raise typed errors from `src.core.media.exceptions`: `StorageWriteError`, `StorageReadError`, `StorageDeleteError`, and `StorageConnectionError`.

## Use local storage

`LocalStorage` (`local.py:19`) stores files under a base directory and validates every path with `os.path.realpath` to prevent directory traversal (`local.py:36`).

```python
from src.core.media.storage import LocalStorage

storage = LocalStorage(base_path="/var/data/uploads", base_url="/media")

# Store and retrieve bytes
storage.store(file_data, "images/ab/cd/photo.jpg", "image/jpeg")
data = storage.retrieve("images/ab/cd/photo.jpg")

# Stream large files
with open("/tmp/video.mp4", "rb") as f:
    storage.store_stream(f, "videos/video.mp4", "video/mp4", size=1024000)

stream, size = storage.retrieve_stream("videos/video.mp4")

# Check and delete
if storage.exists("images/ab/cd/photo.jpg"):
    storage.delete("images/ab/cd/photo.jpg")

url = storage.get_url("images/ab/cd/photo.jpg")
# Returns "/media/images/ab/cd/photo.jpg"

metadata = storage.get_metadata("images/ab/cd/photo.jpg")
# Returns {path, full_path, exists, size, created_at, modified_at}
```

`cleanup_empty_dirs()` (`local.py:179`) removes empty parent directories up to `base_path`.

## Use S3 storage

`S3Storage` (`s3.py:19`) uses `boto3` with SigV4 and path-style addressing. It validates the bucket with `head_bucket` on initialization and retries with standard mode (`s3.py:65`).

```python
from src.core.media.storage import S3Storage

storage = S3Storage(
    bucket="my-bucket",
    access_key="AKIA...",
    secret_key="secret",
    region="us-east-1",
    endpoint_url="https://s3.example.com",  # omit for AWS
    public_url="https://cdn.example.com",
    path_prefix="uploads",
)

storage.store(file_data, "images/photo.jpg", "image/jpeg")
data = storage.retrieve("images/photo.jpg")
url = storage.get_url("images/photo.jpg")
```

### Presigned URLs

Generate temporary URLs for private objects:

```python
# Presigned GET URL
url = storage.generate_presigned_url("images/photo.jpg", expires_in=3600)

# Presigned POST for direct browser upload
post_data = storage.generate_presigned_upload(
    "images/photo.jpg",
    content_type="image/jpeg",
    expires_in=3600,
    max_size=10 * 1024 * 1024,
)
# Returns {url, fields} for a POST request
```

`generate_presigned_url()` is defined in `s3.py:249` and `generate_presigned_upload()` in `s3.py:279`.

### S3 metadata

```python
metadata = storage.get_metadata("images/photo.jpg")
# Returns {path, key, bucket, exists, size, content_type, last_modified, etag}
```

## Use encrypted storage

Wrap any backend to encrypt data at rest:

```python
from src.core.media.storage import LocalStorage, wrap_storage_with_encryption

backend = LocalStorage(base_path="/var/data/uploads")
storage = wrap_storage_with_encryption(backend, key=encryption_key)

storage.store(file_data, "private/doc.pdf", "application/pdf")
assert storage.is_encrypted("private/doc.pdf") is True
```

See `encrypted.py` for `EncryptedStorage` and `wrap_storage_with_encryption()`.

## Handle errors

```python
from src.core.media.exceptions import StorageWriteError, StorageReadError

try:
    storage.store(file_data, path, content_type)
except StorageWriteError as e:
    print(f"Write failed on {e.backend}: {e}")

try:
    data = storage.retrieve(path)
except StorageReadError as e:
    print(f"Read failed: {e}")
```

## Choose a backend

- Use `LocalStorage` for single-node deployments or development.
- Use `S3Storage` for distributed deployments, CDN integration, or when you need presigned URLs.
- Wrap either with `EncryptedStorage` when you need at-rest encryption.

## Related code

- `base.py:9` — `StorageBackendBase` abstract interface
- `local.py:19` — `LocalStorage` with traversal checks in `_full_path()`
- `s3.py:19` — `S3Storage` with `boto3` client setup
- `database.py` — `DatabaseStorage`
- `encrypted.py` — `EncryptedStorage` and `wrap_storage_with_encryption()`
- `__init__.py:1` — public exports
