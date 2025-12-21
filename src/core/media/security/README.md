# Media Security

Security utilities for media handling.

## Components

- `signing.py` - UrlSigner for signed URL generation
- `scanner.py` - MalwareScanner for file scanning
- `proxy.py` - ExternalProxy for proxying external media

## Usage

```python
from src.core.media.security import UrlSigner, MalwareScanner

signer = UrlSigner(secret_key)
signed_url = signer.sign(url, expires_in=3600)

scanner = MalwareScanner()
is_safe = await scanner.scan(file_data)
```
