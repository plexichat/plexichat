# Auth services

The auth services layer contains business logic for authentication-related features. Services coordinate between repositories (data access) and utility modules (encryption, logging) to implement domain rules.

## Architecture

Services sit between the API layer and the repository layer. They own validation, encryption, decryption, and error handling — keeping repositories as thin SQL wrappers.

```
API routes
    ↓
UserNotesService (business logic + encryption)
    ↓
UserNotesRepository (SQL queries)
    ↓
auth_user_notes table
```

## Key files

| File | Purpose |
|------|---------|
| `user_notes.py` | `UserNotesService` — encrypted private notes between users |

## Usage

```python
from src.core.auth.services.user_notes import UserNotesService

service = UserNotesService(db, encryption_enabled=True)

# Create a note
note = service.create_note(user_id=111, target_user_id=222, note="Internal moderation note")

# Read notes
note = service.get_note(user_id=111, target_user_id=222)
all_notes = service.get_all_notes(user_id=111)

# Update a note
updated = service.update_note(user_id=111, target_user_id=222, note="Updated note")

# Delete a note
service.delete_note(user_id=111, target_user_id=222)
```

## Features

- **Encryption at rest** — notes are encrypted with `encrypt_data()` before storage and decrypted on read. If encryption fails, the service logs a warning and falls back to plaintext (`src/core/auth/services/user_notes.py:60`)
- **Upsert behavior** — `create_note()` silently calls `update_note()` if a note already exists for the user pair (`src/core/auth/services/user_notes.py:53`)
- **Self-note prevention** — users cannot create notes about themselves (`src/core/auth/services/user_notes.py:49`)
- **Length validation** — notes cannot exceed 10,000 characters (`src/core/auth/services/user_notes.py:47`)

## Methods

| Method | Description |
|--------|-------------|
| `create_note()` | Create or upsert an encrypted note about another user |
| `update_note()` | Replace an existing note's content |
| `get_note()` | Retrieve and decrypt a single note |
| `get_all_notes()` | Retrieve and decrypt all notes by a user |
| `delete_note()` | Delete a note |

## Related code

- `src/core/auth/repositories/user_notes.py` — data access layer
- `src/utils/encryption.py` — `encrypt_data()` and `decrypt_data()` functions
- `src/core/base.py` — `SnowflakeID`, `timestamp_ms`, `generate_id`
