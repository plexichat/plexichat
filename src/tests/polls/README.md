# Polls tests

This directory contains tests for poll creation, voting, expiry, and results.
The tests verify that polls enforce validation rules, respect permissions,
and produce correct tallies.

## Test files

| File | Description |
|------|-------------|
| `test_create.py` | Poll creation, validation errors, and retrieval |
| `test_expiry.py` | Poll closing, deletion, and expired-poll sweep |
| `test_results.py` | Result retrieval, vote counts, and visibility |
| `test_voting.py` | Vote casting, multiple choice, and duplicate prevention |
| `test_operations.py` | Additional poll operation tests |
| `test_open_findings_regressions.py` | Regression tests for open findings |

## What these tests cover

### Poll creation (`test_create.py`)

- Create a poll with a question, options, and a message
- Create a poll with a duration (`duration_hours`) that sets `ends_at`
- Create a multiple-choice poll (`allow_multiple_choice=True`)
- Create a poll with hidden results (`results_visibility=AFTER_END`)
- Reject empty questions with `InvalidPollQuestionError`
- Reject fewer than 2 options or more than 10 options with
  `PollOptionLimitError`
- Reject invalid durations with `InvalidPollDurationError`
- Retrieve a poll by ID; return `None` for nonexistent polls

### Poll expiry (`test_expiry.py`)

- Close a poll early (only the creator can close)
- Non-creator cannot close a poll (`PermissionDeniedError`)
- Closing an already-ended poll fails with `PollEndedError`
- Voting on a closed poll fails with `PollEndedError`
- Delete a poll (only the creator can delete)
- Non-creator cannot delete a poll (`PermissionDeniedError`)
- `check_expired_polls` sweep ends polls past their `ends_at`
- Open polls survive the sweep and remain votable

### Poll results (`test_results.py`)

- Retrieve results with correct option count
- Vote counts reflect actual votes
- `results_visibility=AFTER_VOTE` hides results until the user votes
- Total vote count is accurate across multiple voters
- User votes are tracked in the results object

### Poll voting (`test_voting.py`)

- Vote successfully on a poll
- Voting updates the option's vote count
- Duplicate votes fail with `AlreadyVotedError`
- Multiple-choice voting records all selected options
- Multiple votes on a single-choice poll fail with
  `MultipleVoteNotAllowedError`
- Voting for an invalid option fails with `PollOptionNotFoundError`
- Voting on a nonexistent poll fails with `PollNotFoundError`

## Running the tests

```bash
# Run all polls tests
pytest src/tests/polls/ -v

# Run creation tests only
pytest src/tests/polls/test_create.py -v

# Run voting tests only
pytest src/tests/polls/test_voting.py -v

# Run with coverage
pytest src/tests/polls/ --cov=src.core.polls -v
```

## Key classes and fixtures

- `TestCreatePoll` -- poll creation and validation
- `TestGetPoll` -- poll retrieval by ID
- `TestClosePoll` -- early close, permission checks, and double-close
- `TestDeletePoll` -- poll deletion and permission checks
- `TestCheckExpiredPolls` -- sweep task for expired polls
- `TestPollResults` -- result retrieval, counts, and visibility
- `TestVoting` -- vote casting, duplicate prevention, and multi-choice

### Fixtures

- `dm_with_message` -- provides two users, a DM, a message, and the
  `PollManager` and `MessagingManager` instances
- `poll_with_options` -- creates a poll with options for result-testing
- `auth_manager`, `messaging_manager`, `poll_manager` -- standalone
  manager fixtures for tests that create their own users

## Related source code

- `src/core/polls/__init__.py` -- `PollManager` and poll exceptions
- `src/core/polls/models.py` -- `Poll`, `PollOption`, `PollResults` models
- `src/core/polls/exceptions.py` -- `InvalidPollQuestionError`,
  `PollOptionLimitError`, `PollEndedError`, `PermissionDeniedError`,
  `PollNotFoundError`, `AlreadyVotedError`, `MultipleVoteNotAllowedError`
