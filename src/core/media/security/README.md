# Media security

Use the media security package to validate, sign, scan, and proxy media in Plexichat.

## Overview

This package provides four security primitives:

| Class | File | Purpose |
|---|---|---|
| `FileValidator` | `validation.py:168` | Magic-byte validation, executable blocking, filename sanitization, and SVG sanitization |
| `UrlSigner` | `signing.py:17` | HMAC-SHA256 URL signing with expiration and user binding |
| `MalwareScanner` | `scanner.py:15` | ClamAV daemon integration for malware scanning |
| `ExternalProxy` | `proxy.py:90` | SSRF-safe fetching, caching, and serving of external media |

All validators read limits from `config.yaml` under `media.security`.

## Validate uploaded files

`FileValidator` (`validation.py:168`) blocks executable uploads and checks that file contents match the declared MIME type.

### Run all checks

Call `validate_file()` to sanitize the filename, block executables, validate magic bytes, and sanitize SVG:

```python
from src.core.media.security import FileValidator

validator = FileValidator()
is_valid, error, safe_name = validator.validate_file(
    file_data, filename="photo.jpg", content_type="image/jpeg"
)

if not is_valid:
    print(f"Rejected: {error}")
```

The method returns `(is_valid, error_message, sanitized_filename)` (`validation.py:315`).

### Validate magic bytes

```python
is_valid, error = validator.validate_magic_bytes(file_data, "image/png")
```

The validator checks signatures defined in `MAGIC_SIGNATURES` (`validation.py:37`). Files smaller than 16 bytes bypass this check. Unknown content types pass through.

### Block executables

```python
is_blocked, error = validator.is_executable_blocked("setup.exe", "application/octet-stream")
```

Blocked extensions (`validation.py:93`) include `.exe`, `.sh`, `.dll`, `.jar`, `.py`, and others. Blocked MIME types (`validation.py:152`) include `application/x-executable` and `application/x-msdownload`.

### Sanitize filenames

```python
from src.core.media.security import sanitize_filename

safe = sanitize_filename("../../etc/passwd")
# Returns "passwd"
```

`sanitize_filename()` (`validation.py:272`) removes path separators, null bytes, `..` sequences, control characters, and truncates overlong names. It never returns an empty string.

### SVG sanitization

When `content_type` is `image/svg+xml`, `validate_file()` parses the file with `defusedxml` and walks the XML tree against an element allow-list (`validation.py:375`) and an attribute allow-list (`validation.py:443`). The validator blocks:

- Elements outside the allow-list (such as `script`, `iframe`, or `foreignObject`).
- Event-handler attributes (`on*`).
- Unsafe URI schemes (`javascript:`, `vbscript:`, `file:`) and external references in `<use>`, `<image>`, and `<style>`.

The validator fails closed on parse errors and requires `defusedxml` at import time (`validation.py:20`).

### Convenience functions

```python
from src.core.media.security import validate_file, sanitize_filename

is_valid, error, safe_name = validate_file(file_data, filename, content_type)
safe_name = sanitize_filename(filename)
```

## Sign and verify URLs

`UrlSigner` (`signing.py:17`) creates HMAC-SHA256 signed URLs with an expiration timestamp and optional user binding.

### Sign a URL

```python
from src.core.media.security import UrlSigner

signer = UrlSigner(secret_key="your-secret", default_expiry=3600)
signed = signer.sign_url(
    url="/media/ab/cd/file.jpg",
    file_id=42,
    user_id=current_user_id,  # binds the URL to this user
    expires_in=3600,
)

print(signed.url)        # URL with fid, exp, sig, uid params
print(signed.expires_at) # milliseconds since epoch
```

### Verify a URL

```python
from src.core.media.exceptions import SignatureExpiredError, SignatureInvalidError

try:
    is_valid, file_id = signer.verify_url(signed.url, current_user_id=current_user_id)
except SignatureExpiredError:
    print("URL expired")
except SignatureInvalidError:
    print("Invalid signature or unauthorized user")
```

`verify_url()` (`signing.py:80`) checks the `sig`, `exp`, `fid`, and `uid` query parameters, verifies expiration, enforces user binding, and compares the HMAC with `hmac.compare_digest`.

### Check expiration

```python
if signer.is_expired(signed.url):
    print("Expired")

expiry_ms = signer.get_expiry_time(signed.url)
```

## Scan for malware

`MalwareScanner` (`scanner.py:15`) communicates with a ClamAV daemon over TCP.

### Configure and check availability

```python
from src.core.media.security import MalwareScanner

scanner = MalwareScanner(host="localhost", port=3310, timeout=30, enabled=True)

if not scanner.is_available():
    print("ClamAV not reachable")
```

`is_available()` (`scanner.py:41`) sends `PING` and expects `PONG`.

### Scan bytes or files

```python
from src.core.media.models import ScanStatus

status, threat = scanner.scan_bytes(file_data)
# status is ScanStatus.CLEAN, INFECTED, ERROR, or SKIPPED

status, threat = scanner.scan_file("/tmp/upload.pdf")
if status == ScanStatus.INFECTED:
    print(f"Infected: {threat}")
```

When `enabled` is `False`, both methods return `(ScanStatus.SKIPPED, None)` without connecting.

### Manage the daemon

```python
version = scanner.get_version()
reloaded = scanner.reload_database()
```

## Proxy external media

`ExternalProxy` (`proxy.py:90`) fetches external URLs through an SSRF-safe path, validates content types and magic bytes, and caches the result in your storage backend.

### Initialize

```python
from src.core.media.security import ExternalProxy

proxy = ExternalProxy(
    storage_backend=storage,
    db=db,
    cache_path="proxy_cache",
    cache_ttl=86400,
    max_size=10 * 1024 * 1024,
    allowed_types={"image/jpeg", "image/png", "image/gif", "image/webp"},
)
```

`ExternalProxy` requires `requests` and `urllib3` (`proxy.py:14`). It rejects `image/svg+xml` by default because SVG can carry scripts.

### Fetch and serve

```python
# Fetch and cache
proxied = proxy.fetch("https://example.com/image.jpg")

# Get bytes
data, content_type = proxy.get_content("https://example.com/image.jpg")

# Get a stream
stream, size, content_type = proxy.get_content_stream("https://example.com/image.jpg")

# Cache check
if proxy.is_cached(url):
    print("Cache hit")
```

`fetch()` (`proxy.py:164`) validates the URL through `URLValidator`, checks the cache, fetches via the resolved IP (to prevent DNS rebinding), validates `Content-Type` and magic bytes (`proxy.py:445`), and stores the result through `ResponseStreamWrapper` (`proxy.py:30`) to avoid loading large files into memory.

### Invalidate and clean up

```python
proxy.invalidate("https://example.com/image.jpg")
removed = proxy.cleanup_expired()
```

## Related code

- `validation.py:168` — `FileValidator`, `MAGIC_SIGNATURES`, `BLOCKED_EXTENSIONS`
- `signing.py:17` — `UrlSigner`
- `scanner.py:15` — `MalwareScanner`
- `proxy.py:90` — `ExternalProxy` and `ResponseStreamWrapper`
- `__init__.py:1` — public exports
