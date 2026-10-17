# Polls

Create and manage message polls with configurable options, duration, and results visibility.

## Overview

The polls module attaches polls to messages and provides voting, results, and lifecycle operations. Each poll supports 2 to 10 options, single or multiple choice voting, and automatic expiry.

## Features

- 2 to 10 options per poll.
- Duration from 1 hour to 7 days, or no expiry.
- Single or multiple choice voting.
- Results visibility control: always, after vote, or after end.
- Per-user vote tracking with database-enforced uniqueness.
- Early closure by the creator and automatic expiry handling.

## Before you begin

Initialize the module once at startup. The messaging module is required for ownership and channel access checks.

```python
from src.core.database import Database
from src.core import auth, messaging, polls

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
polls.setup(db, messaging_module=messaging, auth_module=auth)
```

If you omit `messaging_module`, calls that need message authorization raise `PollConfigurationError`.

## Create a poll

```python
from src.core import polls

poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="What's your favorite programming language?",
    options=["Python", "JavaScript", "Rust", "Go"],
    duration_hours=24,
)
```

### Multiple choice

```python
poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="Which features do you want?",
    options=["Dark mode", "Mobile app", "API access", "Plugins"],
    duration_hours=168,
    allow_multiple_choice=True,
)
```

### Results visibility

```python
poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="Who will win?",
    options=["Team A", "Team B", "Draw"],
    duration_hours=48,
    results_visibility=polls.PollResultsVisibility.AFTER_END,
)
```

| Visibility | Behavior |
|------------|----------|
| `ALWAYS` | Results are visible to everyone immediately. |
| `AFTER_VOTE` | Results are visible only after you vote. |
| `AFTER_END` | Results are visible only after the poll ends. |

### No expiry

```python
poll = polls.create_poll(
    user_id=user_id,
    message_id=message_id,
    question="Permanent poll?",
    options=["Yes", "No"],
    duration_hours=None,
)
```

Only the message author can create a poll on that message.

## Vote on a poll

```python
# Single choice
results = polls.vote(user_id=user_id, poll_id=poll.id, option_ids=[option1_id])

# Multiple choice (if the poll allows it)
results = polls.vote(
    user_id=user_id,
    poll_id=poll.id,
    option_ids=[option1_id, option3_id],
)
```

To revise a vote, call `change_vote` instead of voting again:

```python
results = polls.change_vote(user_id=user_id, poll_id=poll.id, option_ids=[option2_id])
```

To remove a vote entirely:

```python
removed = polls.remove_vote(user_id=user_id, poll_id=poll.id)
```

## Get results

Results respect the poll's visibility setting. If you are not entitled to see results, vote counts and the voter total return as `None`.

```python
results = polls.get_results(poll_id, user_id)

print(f"Question: {results.poll.question}")
print(f"Total votes: {results.total_votes}")
print(f"You voted: {results.user_voted}")

for option in results.options:
    if option.vote_count is None:
        print(f"{option.text}: results hidden")
    else:
        pct = (option.vote_count / results.total_votes * 100) if results.total_votes else 0
        print(f"{option.text}: {option.vote_count} votes ({pct:.1f}%)")
```

To get the voter roster (creator or users with `admin.polls.view_voters` only):

```python
voters = polls.get_voters(poll_id=poll.id, user_id=creator_id)
```

## Close or delete a poll

Only the creator can close or delete a poll.

```python
# Close before expiry
poll = polls.close_poll(user_id, poll_id)

# Delete entirely
polls.delete_poll(user_id, poll_id)
```

## Check for expired polls

Call `check_expired_polls` periodically from a background task. It marks expired polls as ended and emits `POLL_UPDATE` gateway events.

```python
ended_count = polls.check_expired_polls()
print(f"Ended {ended_count} expired polls")
```

Example scheduler:

```python
import schedule
import time

def poll_expiry_task():
    ended = polls.check_expired_polls()
    if ended > 0:
        print(f"Ended {ended} expired polls")

schedule.every(1).minutes.do(poll_expiry_task)

while True:
    schedule.run_pending()
    time.sleep(1)
```

## Configuration

Configure `config/config.yaml` under `polls`:

```yaml
polls:
  min_options: 2
  max_options: 10
  min_duration_hours: 1
  max_duration_hours: 168  # 7 days
  max_question_length: 300
  max_option_length: 100
```

Set `encryption.encrypt_polls: true` to store questions and options encrypted at rest. The plaintext columns are cleared when encryption is enabled.

## Poll states

| State | Description |
|-------|-------------|
| Active | Open for voting. |
| Ended | Expiry time reached or closed early (`ended_at` is set). |
| No expiry | Remains open until manually closed (`ends_at` is `None`). |

## Error handling

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
    print("This poll has ended.")
except AlreadyVotedError:
    print("You already voted. Call change_vote() to revise.")
except MultipleVoteNotAllowedError:
    print("This poll allows only one choice.")
except PollOptionLimitError as e:
    print(f"Poll must have {e.min_options}-{e.max_options} options.")
```

## Database schema

| Table | Description |
|-------|-------------|
| `poll_polls` | Poll metadata (question, creator, expiry, visibility). |
| `poll_options` | Options for each poll. |
| `poll_votes` | Per-option votes (`UNIQUE(poll_id, option_id, user_id)`). |
| `poll_voters` | Voter registry (`PRIMARY KEY(poll_id, user_id)`) that enforces one vote per user per poll. |

## Testing

```bash
pytest src/tests/polls/ -v
```
