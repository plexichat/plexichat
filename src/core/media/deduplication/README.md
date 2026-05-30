# Media Deduplication Package

Hash-based file deduplication and content reporting for Plexichat media uploads.

## Structure

- `constants.py` - Enums, dataclasses, SQL schema, and setup functions
- `base.py` - `DeduplicationManagerBase` with core initialization and config loading
- `hashing.py` - `HashOperationsMixin` for SHA-256 and perceptual hashing
- `dedup.py` - `DeduplicationMixin` for duplicate detection and reference counting
- `blocking.py` - `BlockingMixin` for hash and user blocking operations
- `reporting.py` - `ReportingMixin` for content reporting and moderation
- `composer.py` - `DeduplicationManager` class composed from all mixins
- `__init__.py` - Backward-compatible re-exports

## Usage

```python
from src.core.media.deduplication import DeduplicationManager

manager = DeduplicationManager(db)

# Check for duplicate
result = manager.check_duplicate(file_data, content_type)
if result.is_duplicate:
    print(f"Already exists: {result.existing_url}")

# Register new file
manager.register_file(hash_value, file_size, content_type, storage_path, storage_backend, timestamp)

# Block a hash
manager.block_hash(hash_value, "Violates policy", blocked_by=admin_id)

# Report content
manager.report_hash(hash_value, reporter_id, "Copyright infringement")
```

## Architecture

`DeduplicationManager` uses multiple inheritance (mixin pattern):

```
DeduplicationManager
├── DeduplicationManagerBase (init, config)
├── HashOperationsMixin (compute_hash, compute_phash)
├── DeduplicationMixin (check_duplicate, register_file)
├── BlockingMixin (is_blocked, block_hash)
└── ReportingMixin (report_hash, review_report)
```