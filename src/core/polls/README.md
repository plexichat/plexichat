# Polls Module

Message poll system for Plexichat API supporting poll creation, voting, results visibility control, and automatic expiry handling.

## Features

- Create polls attached to messages
- Poll options (2-10 choices)
- Poll duration (1 hour to 7 days or no expiry)
- Single or multiple choice voting
- Poll results visibility control (always, after vote, after end)
- Vote tracking per user
- Poll end handling with final results
- Early poll closure by creator
- Automatic expiry checking

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import polls

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)

# Initialize polls
polls.setup(db, messaging)
```

## Usage

### Create Poll

```python
from src.core import polls

# Create a simple poll
poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="What's your favorite programming language?",
    options=["Python", "JavaScript", "Rust", "Go"],
    duration_hours=24
)

# Create a multiple choice poll
poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="Which features do you want?",
    options=["Dark mode", "Mobile app", "API access", "Plugins"],
    duration_hours=168,
    allow_multiple_choice=True
)

# Create a poll with hidden results until end
poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="Who will win?",
    options=["Team A", "Team B", "Draw"],
    duration_hours=48,
    results_visibility=polls.PollResultsVisibility.AFTER_END
)

# Create a poll with no expiry
poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="Permanent poll question?",
    options=["Yes", "No"],
    duration_hours=None
)
```

### Vote on Poll

```python
# Single choice vote
results = polls.vote(
    user_id=user_id,
    poll_id=poll.id,
    option_ids=[option1_id]
)

# Multiple choice vote (if allowed)
results = polls.vote(
    user_id=user_id,
    poll_id=poll.id,
    option_ids=[option1_id, option3_id]
)
```

### Get Results

```python
# Get poll results
results = polls.get_results(poll_id, user_id)

print(f"Question: {results.poll.question}")
print(f"Total votes: {results.total_votes}")
print(f"You voted: {results.user_voted}")

for option in results.options:
    percentage = (option.vote_count / results.total_votes * 100) if results.total_votes > 0 else 0
    print(f"{option.text}: {option.vote_count} votes ({percentage:.1f}%)")
```

### Close Poll Early

```python
# Creator can close poll before expiry
poll = polls.close_poll(user_id, poll_id)
```

### Delete Poll

```python
# Creator can delete poll
polls.delete_poll(user_id, poll_id)
```

### Check Expired Polls

```python
# Background task to end expired polls
ended_count = polls.check_expired_polls()
print(f"Ended {ended_count} expired polls")
```

## Configuration

Settings in `config/config.yaml` under `polls`:

```yaml
polls:
  min_options: 2
  max_options: 10
  min_duration_hours: 1
  max_duration_hours: 168  # 7 days
  max_question_length: 300
  max_option_length: 100
```

## Results Visibility

| Mode | Description |
|------|-------------|
| ALWAYS | Results visible to everyone immediately |
| AFTER_VOTE | Results visible only after user votes |
| AFTER_END | Results visible only after poll ends |

## Poll States

- **Active**: Poll is open for voting
- **Ended**: Poll has reached expiry time or was closed early
- **No Expiry**: Poll remains open indefinitely until manually closed

## Error Handling

All poll errors inherit from `PollError`:

```python
from src.core.polls import (
    PollError,
    PollNotFoundError,
    PollEndedError,
    InvalidPollQuestionError,
    InvalidPollOptionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
    PermissionDeniedError,
)

try:
    polls.vote(user_id, poll_id, [option_id])
except PollEndedError:
    print("This poll has ended")
except AlreadyVotedError:
    print("You have already voted")
except MultipleVoteNotAllowedError:
    print("This poll only allows single choice")
except PollOptionLimitError as e:
    print(f"Poll must have {e.min_options}-{e.max_options} options")
```

## Database Schema

Tables (prefixed with `poll_`):
- `poll_polls` - Poll metadata
- `poll_options` - Poll options
- `poll_votes` - User votes

## Testing

```bash
pytest src/tests/polls/ -v
```

## Integration with Messaging

Polls are attached to messages via `message_id`. The messaging module should handle poll rendering and display. When a message with a poll is deleted, the poll should also be deleted.

## Background Tasks

The `check_expired_polls()` function should be called periodically (e.g., every minute) by a background task to automatically end polls that have reached their expiry time.

Example with a scheduler:

```python
import schedule
import time

def poll_expiry_task():
    ended = polls.check_expired_polls()
    if ended > 0:
        logger.info(f"Ended {ended} expired polls")

schedule.every(1).minutes.do(poll_expiry_task)

while True:
    schedule.run_pending()
    time.sleep(1)
```

## Best Practices

1. **Question Length**: Keep questions concise and clear
2. **Option Count**: 2-5 options work best for most polls
3. **Duration**: Use 24-168 hours for time-sensitive polls
4. **Multiple Choice**: Only enable when truly needed
5. **Results Visibility**: Use AFTER_END for competitive polls to prevent bias
