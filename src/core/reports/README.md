# Reports

Report messages and users for moderation review.

## Overview

The reports module captures message and user reports, preserves evidence, and provides an admin review workflow. Message reports can automatically generate media hash reports for attachments, and both report types support assignment, escalation, and resolution tracking.

Use this module when you need to let users flag content or behavior and let moderators triage, review, and resolve those flags.

## Before you begin

Initialize the module once at startup with a connected database instance. Optionally pass a messaging module to enable automatic media hash reporting.

```python
from plexichat.src.core.database import Database
from plexichat.src.core import reports

db = Database()
# Connect db ...

reports.setup(db)
```

`__init__.py:107` sets the singleton. Call `reports.create_tables(db)` to create tables, which delegates to `schema.py:124` and creates three tables: `reports`, `message_reports`, and `user_reports`. Configuration values are read under the `reports` key via `utils.config` (`__init__.py:143`).

## Report a message

Report a specific message for harassment, spam, or other categories. `message_content` is stored as a snapshot of the message at report time.

```python
from plexichat.src.core import reports

report = reports.report_message(
    reporter_id=123456789,
    message_id=987654321,
    channel_id=111222333,
    reason="User is being very mean",
    category="harassment",
    message_content="This is the bad message content",
)
```

Full signature (`__init__.py:152`):

```python
reports.report_message(
    reporter_id, message_id, channel_id, reason,
    category="other",
    details=None,
    server_id=None,
    reported_user_id=None,
    message_content=None,
)
```

If a messaging module is configured and the message has attachments with checksums, `report_message` also inserts rows into `media_hash_reports` for file-level moderation (`__init__.py:195`).

To avoid duplicate moderation queue entries, check for an existing pending report first:

```python
existing = reports.find_open_message_report(reporter_id, message_id)
if existing is None:
    reports.report_message(reporter_id, message_id, channel_id, reason, category)
```

## Report a user

Report a user for general behavior that is not tied to a single message.

```python
report = reports.report_user(
    reporter_id=123456789,
    reported_user_id=444555666,
    reason="This user keeps sending spam in multiple channels",
    category="spam",
    evidence_message_ids=[987654321, 987654322],
)
```

Full signature (`__init__.py:455`):

```python
reports.report_user(
    reporter_id, reported_user_id, reason,
    category="other",
    details=None,
    evidence_message_ids=None,
)
```

Evidence IDs are stored as a comma-separated string (`__init__.py:483`) and parsed back on read (`__init__.py:623`).

Check idempotency the same way as for messages:

```python
existing = reports.find_open_user_report(reporter_id, reported_user_id)
```

## Review reports

### Get reports

```python
# Get pending message reports
pending = reports.get_message_reports(status_filter="pending", limit=50, offset=0)

# Get a single report by ID
report = reports.get_message_report(report_id)

# Count reports by status
counts = reports.get_message_report_counts()
# {"pending": 5, "reviewed": 2, "actioned": 10, "dismissed": 3, "total": 20}

# User reports
user_reports = reports.get_user_reports(status_filter="pending")
user_counts = reports.get_user_report_counts()
```

### Review and resolve

```python
# Review a message report
reports.review_message_report(
    report_id=report.id,
    admin_id=admin_user_id,
    action="action",  # "action", "dismiss", or "review"
    notes="User has been banned for 24 hours",
)

# Review a user report
reports.review_user_report(
    report_id=report.id,
    admin_id=admin_user_id,
    action="dismiss",
    notes="No violation found",
)
```

Action mapping (`__init__.py:367`):

* `action` → `actioned`
* `dismiss` → `dismissed`
* Any other value → `reviewed`

The review updates `status`, `reviewed_at`, `reviewed_by`, `admin_notes`, and `action_taken`. The method returns `True` if a row was affected (`__init__.py:382`).

## Data models

### MessageReport

Defined in `__init__.py:52`:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `int` | Snowflake report ID. |
| `message_id`, `channel_id`, `server_id` | `int` | Location of the reported message. |
| `reporter_id`, `reported_user_id` | `int` | Reporter and reported user. |
| `reason` | `str` | Brief reason. |
| `category` | `str` | Category string (see below). |
| `details`, `message_content`, `evidence_urls` | `str \| None` | Extended details and snapshot. |
| `priority`, `status` | `str`, `ReportStatus` | Triage fields. |
| `reported_at`, `reviewed_at`, `reviewed_by`, `admin_notes`, `action_taken` | `int \| None` | Review metadata. |
| `assigned_to`, `escalated_at`, `resolution` | `int \| str \| None` | Assignment and resolution. |

### UserReport

Defined in `__init__.py:79` with the same review and assignment fields, plus `evidence_message_ids: list[int] | None`.

### Status values

Defined in `__init__.py:29`:

| Status | Description |
|--------|-------------|
| `PENDING` | Newly created, awaiting review. |
| `REVIEWED` | Viewed but no further action taken. |
| `ACTIONED` | Disciplinary action taken. |
| `DISMISSED` | No action necessary. |

### Categories

Defined in `__init__.py:38`:

`HARASSMENT`, `SPAM`, `INAPPROPRIATE`, `ILLEGAL`, `HATE_SPEECH`, `THREATS`, `IMPERSONATION`, `OTHER`. Pass lower-case strings such as `"harassment"` to `report_message` and `report_user`; the schema defaults to `"other"` (`schema.py:69`).

## Database schema

Defined in `schema.py:15`:

* `reports` — Unified table for the enhanced reports feature (`schema.py:17`). Includes a partial unique index on `(reporter_id, report_type, target_id)` for active statuses to prevent concurrent duplicates (`schema.py:55`).
* `message_reports` — Per-message reports (`schema.py:61`).
* `user_reports` — Per-user reports (`schema.py:94`).

Indexes exist on `status`, `reporter_id`, `reported_user_id`, `priority`, and `assigned_to` for admin queue queries.

## Handle errors

The module raises `RuntimeError` if you call any data function before `setup`:

```python
try:
    reports.report_message(reporter_id, message_id, channel_id, reason, category)
except RuntimeError as e:
    print(f"Not initialized: {e}")
```

Message and user reports use the same review error pattern: `review_*` returns `False` if no row matched the ID. Check the return value to detect stale or deleted reports.
