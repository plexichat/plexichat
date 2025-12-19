# Reports Module

Message and user behavior reporting system for content moderation.

## Features

- **Message Reporting**: Report specific messages for harassment, spam, etc.
- **User Reporting**: Report users for general behavior issues.
- **Snapshots**: Captures message content at the time of reporting.
- **Admin Review**: Full workflow for admins to review, dismiss, or action reports.
- **History Tracking**: Tracks which admin reviewed which report and what action was taken.

## Usage

### Setup

```python
from src.core.database import Database
from src.core import reports

db = Database()
# ... connect db ...

reports.setup(db)
```

### Reporting a Message

```python
from src.core import reports

report = reports.report_message(
    reporter_id=123456789,
    message_id=987654321,
    channel_id=111222333,
    reason="User is being very mean",
    category="harassment",
    message_content="This is the bad message content"
)
```

### Reporting a User

```python
report = reports.report_user(
    reporter_id=123456789,
    reported_user_id=444555666,
    reason="This user keeps sending spam in multiple channels",
    category="spam",
    evidence_message_ids=[987654321, 987654322]
)
```

### Admin Review

```python
# Get pending reports
pending = reports.get_message_reports(status_filter="pending")

# Review a report
reports.review_message_report(
    report_id=report.id,
    admin_id=admin_user_id,
    action="action",  # 'action', 'dismiss', or 'review'
    notes="User has been banned for 24 hours"
)
```

## Data Models

### Report Statuses

- `PENDING`: Newly created report.
- `REVIEWED`: Admin has looked at it but taken no further action.
- `ACTIONED`: Admin has taken disciplinary action.
- `DISMISSED`: Admin determined no action was necessary.

### Report Categories

- `HARASSMENT`
- `SPAM`
- `INAPPROPRIATE`
- `ILLEGAL`
- `HATE_SPEECH`
- `THREATS`
- `IMPERSONATION`
- `OTHER`
