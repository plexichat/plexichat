# Feedback module

The feedback module handles user feedback submission and storage. Use this module to collect categorized feedback with optional ratings and to integrate feedback tickets with the admin dashboard.

## Before you begin

Initialize the module with a database handle before you call any other function.

```python
from src.core.feedback import setup

setup(db)
```

If you call `submit_feedback()` or `get_feedback_by_id()` before setup, the module raises `RuntimeError`. Use `is_setup()` to check initialization state.

## Create the table

Call `create_tables()` once during database initialization to ensure the `feedback` table exists.

```python
from src.core import feedback

feedback.create_tables(db)
```

The table is defined in `__init__.py:56`:

```sql
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    category TEXT,
    rating INTEGER,
    created_at INTEGER NOT NULL,
    status TEXT DEFAULT 'open',
    resolved_at INTEGER,
    resolved_by INTEGER,
    internal_notes TEXT,
    internal_notes_encrypted TEXT,
    internal_notes_format TEXT DEFAULT 'plain',
    FOREIGN KEY (user_id) REFERENCES auth_users(id)
)
```

## Submit feedback

Use `submit_feedback()` to store a new entry. The function generates a Snowflake ID and returns it.

```python
from src.core import feedback

feedback_id = feedback.submit_feedback(
    user_id=123,
    content="The new voice feature is great!",
    category="feature_request",
    rating=5,
)
```

Parameters:

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | `int` | Yes | ID of the user submitting feedback. |
| `content` | `str` | Yes | Feedback text. |
| `category` | `str \| None` | No | Category label, such as `bug` or `feature_request`. |
| `rating` | `int \| None` | No | Numeric rating, if applicable. |

The function is defined in `__init__.py:84`. It records `created_at` as the current time in milliseconds.

## Retrieve feedback

Use `get_feedback_by_id()` to fetch a single entry.

```python
from src.core import feedback

entry = feedback.get_feedback_by_id(feedback_id)
if entry:
    print(entry.content, entry.status)
```

The function is defined in `__init__.py:108`. It returns a `FeedbackEntry` dataclass or `None` if the ID is not found.

## FeedbackEntry fields

`FeedbackEntry` is defined in `__init__.py:17`.

| Field | Type | Description |
| --- | --- | --- |
| `id` | `int` | Snowflake ID for the entry. |
| `user_id` | `int` | ID of the submitting user. |
| `content` | `str` | Feedback text. |
| `category` | `str \| None` | Category label. |
| `rating` | `int \| None` | Numeric rating. |
| `created_at` | `int` | Creation timestamp in milliseconds. |
| `status` | `str` | Ticket status. Defaults to `"open"`. |
| `resolved_at` | `int \| None` | Timestamp when the ticket was resolved. |
| `resolved_by` | `int \| None` | ID of the admin who resolved the ticket. |
| `internal_notes` | `str \| None` | Admin-only notes. |
| `internal_notes_encrypted` | `str \| None` | Encrypted admin notes. |
| `internal_notes_format` | `str \| None` | Format for internal notes. Defaults to `"plain"`. |

## Related modules

- `src.utils.encryption.generate_snowflake_id` — Generates IDs for new feedback entries.
- Admin dashboard ticket management — Consumes feedback entries for triage and resolution.
