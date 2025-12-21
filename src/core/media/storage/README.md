# Media Storage

Storage backends for uploaded media files.

## Backends

- `local.py` - LocalStorage for filesystem storage
- `s3.py` - S3Storage for AWS S3 / compatible storage

## Usage

```python
from src.core.media.storage import LocalStorage, S3Storage

storage = LocalStorage(base_path="/uploads")
url = await storage.store(file_data, filename)
data = await storage.retrieve(filename)
```

## Base Class

All backends extend `StorageBackendBase` with methods for store, retrieve, and delete.
