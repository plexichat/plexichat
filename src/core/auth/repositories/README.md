# Auth repositories

The auth repositories layer provides data access objects (DAOs) for authentication-related database operations. Each repository encapsulates SQL queries for a specific domain entity, keeping data access logic separate from business logic.

## Architecture

Repositories follow a simple pattern: they accept a database instance in the constructor and expose methods that map to individual SQL operations. No business logic, encryption, or validation is performed at this layer — those concerns live in the corresponding service layer.

```
Auth repositories
└── UserNotesRepository  — CRUD for the auth_user_notes table
```

## Key files

| File | Purpose |
|------|---------|
| `user_notes.py` | `UserNotesRepository` — create, update, read, delete, and check existence of user notes |

## Usage

```python
from src.core.auth.repositories.user_notes import UserNotesRepository

repo = UserNotesRepository(db)
repo.create(note_id, user_id, target_user_id, note_encrypted, created_at, updated_at)
note = repo.get(user_id, target_user_id)
all_notes = repo.get_all(user_id)
repo.update(note_id, note_encrypted, updated_at)
repo.delete(user_id, target_user_id)
exists = repo.exists(user_id, target_user_id)
```

## Methods

| Method | Description |
|--------|-------------|
| `create()` | Insert a new note into `auth_user_notes` |
| `update()` | Update an existing note by ID |
| `get()` | Retrieve a specific note by author and target user |
| `get_all()` | Retrieve all notes by a user, ordered by `updated_at` descending |
| `delete()` | Delete a note by author and target user |
| `exists()` | Check whether a note exists between two users |

## Related code

- `src/core/auth/services/user_notes.py` — business logic that uses this repository
- `src/core/base.py` — `SnowflakeID` type alias
