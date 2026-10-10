# Media deduplication

Use the media deduplication package to detect duplicate uploads, enforce blocklists, and manage content reports in Plexichat.

The package hashes uploaded files with SHA-256 and perceptual hashing (pHash), stores a single copy per unique hash, and maintains reference counts, blocked hashes, and moderation reports.

## Architecture

`DeduplicationManager` is composed from a base class and mixins through multiple inheritance:

```
DeduplicationManager
├── DeduplicationManagerBase  — initialization and configuration
├── HashOperationsMixin       — compute_hash, compute_phash
├── DeduplicationMixin       — check_duplicate, register_file, reference counting
├── BlockingMixin            — is_blocked, block_hash, block_user
└── ReportingMixin           — report_hash, review_report
```

### Module structure

| File | Purpose |
|---|---|
| `constants.py` | `HashAlgorithm`, `ReportStatus` enums; `FileHash`, `HashReport`, `DeduplicationResult` dataclasses; SQL `SCHEMA`, `setup()`, and `create_tables()` |
| `base.py` | `DeduplicationManagerBase` with database handle and config loading |
| `hashing.py` | `HashOperationsMixin` for SHA-256, SHA-512, BLAKE2b, and pHash computation |
| `dedup.py` | `DeduplicationMixin` for duplicate detection and reference counting |
| `blocking.py` | `BlockingMixin` for hash and user blocking (`media_blocked_hashes`, `media_blocked_users`) |
| `reporting.py` | `ReportingMixin` for content reports and moderation review |
| `composer.py` | `DeduplicationManager` that combines all mixins |
| `__init__.py` | Public re-exports for backward compatibility |

### Database tables

The `SCHEMA` in `constants.py:76` creates the following tables:

- `media_file_hashes` — canonical hash records with `phash_value`, `reference_count`, and storage location.
- `media_hash_reports` — user-submitted reports with `status`, `reporter_id`, and review fields.
- `media_blocked_hashes` — blocked hashes by type (`sha256` or `phash`) with optional `phash_threshold`.
- `media_blocked_users` — users banned from uploading.

## Prerequisites

Initialize the database and create deduplication tables before you use the manager:

```python
from src.core.media.deduplication import create_tables, DeduplicationManager

create_tables(db)
manager = DeduplicationManager(db)
```

Use `setup(db)` only if you need the no-op hook for module registration; `create_tables(db)` creates the tables.

## Manage deduplication

### Check for a duplicate

Call `check_duplicate()` to hash the file and look up an existing record. Check `is_blocked` before you store the file.

```python
from src.core.media.deduplication import DeduplicationManager

manager = DeduplicationManager(db)
result = manager.check_duplicate(file_data, content_type)

if result.is_blocked:
    raise ValueError(f"Blocked content: {result.block_reason}")

if result.is_duplicate:
    # Reuse the existing object instead of storing a new copy.
    print(result.existing_url)
```

`DeduplicationResult` contains `is_duplicate`, `hash_value`, `existing_file_id`, `existing_url`, `is_blocked`, and `block_reason` as defined in `constants.py:64`.

### Register a new file

Call `register_file()` after you store a file to create or increment its hash record:

```python
manager.register_file(
    hash_value=result.hash_value,
    file_size=len(file_data),
    content_type="image/jpeg",
    storage_path="media/ab/cd/file.jpg",
    storage_backend="local",
    timestamp=now_ms,
)
```

### Compute hashes directly

Use `HashOperationsMixin` methods when you need the hash value without a deduplication check:

```python
sha256_hash = manager.compute_hash(file_data, algorithm="sha256")
phash = manager.compute_phash(image_data)  # perceptual hash for images
```

Supported algorithms are listed in `HashAlgorithm` (`constants.py:14`): `SHA256`, `SHA512`, `BLAKE2B`, and `PHASH`.

## Manage blocklists

### Block a hash

```python
manager.block_hash(hash_value, reason="Violates policy", blocked_by=admin_id)
```

### Check whether a hash or user is blocked

```python
if manager.is_blocked(hash_value):
    print("Upload rejected")

if manager.is_user_blocked(user_id):
    print("User banned from uploading")
```

## Manage content reports

### Report content

```python
report = manager.report_hash(
    hash_value=hash_value,
    reporter_id=user_id,
    reason="Copyright infringement",
    details="Original source URL",
)
```

### Review a report

```python
manager.review_report(
    report_id=report.id,
    reviewed_by=moderator_id,
    status=ReportStatus.BLOCKED,  # or CLEARED, REVIEWED
    admin_notes="Confirmed violation",
)
```

`ReportStatus` values are `PENDING`, `REVIEWED`, `BLOCKED`, and `CLEARED` (`constants.py:23`).

## Reference counting

The manager tracks `reference_count` in `media_file_hashes`. When you delete a file that shares a hash, decrement the count instead of deleting the stored object. Delete the stored object only when the count reaches zero.

## Related code

- `composer.py:12` — `DeduplicationManager` composition
- `constants.py:32` — `FileHash`, `HashReport`, and `DeduplicationResult` definitions
- `hashing.py` — `HashOperationsMixin` implementation
- `blocking.py` — `BlockingMixin` implementation
- `reporting.py` — `ReportingMixin` implementation
