# DM Anti-Spam Module

Provides rate limiting, duplicate message detection, and pattern-based spam filtering for DM (direct message) conversations.

## Files

- `__init__.py` - Module exports (`DMSpamDetector`)
- `detector.py` - Core spam detection engine with in-memory tracking and cache-backed state

## Key Classes

### `DMSpamDetector`

Main spam detection service with the following checks:

1. **Rate limiting** - Per-sender/recipient message rate tracking with configurable windows
2. **Duplicate detection** - Identical content hash tracking within a time window
3. **Pattern-based detection** - URL count, mention spam, invite link patterns

### Configuration

- `DEFAULT_RATE_LIMIT` - Max messages per window (default: 10)
- `DEFAULT_RATE_WINDOW` - Window in seconds (default: 60)
- `DEFAULT_DUPLICATE_THRESHOLD` - Max identical messages (default: 3)
- `DEFAULT_DUPLICATE_WINDOW` - Window in seconds (default: 300)
- `DEFAULT_NEW_USER_RATE_LIMIT` - Stricter limit for new accounts (default: 5)
- `DEFAULT_NEW_USER_AGE_MS` - Account age threshold for "new user" (default: 7 days)

### Memory Management

In-memory trackers (`_rate_tracker`, `_duplicate_tracker`) are periodically pruned via `_prune_trackers()` with:
- TTL-based entry eviction (10 minutes)
- Hard cap on max keys (10,000) per tracker
- Pruning interval of 5 minutes

### Usage

```python
from src.core.antispam import DMSpamDetector

detector = DMSpamDetector(db, auth_module)
allowed, reason = detector.check_message(sender_id, recipient_id, content)
if not allowed:
    # Block the message, return error to user
    pass
```
