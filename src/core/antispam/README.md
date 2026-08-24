# DM Anti-Spam Module

Provides rate limiting, duplicate message detection, and pattern-based spam filtering for DM (direct message) conversations.

The detector is wired into the live message pipeline: `POST /channels/{id}/messages` invokes it for DM conversations (`_enforce_dm_spam_protection` in `src/api/routes/messages/messages_crud/base.py`), and the module is initialized during server bootstrap alongside automod.

## Files

- `__init__.py` - Module exports plus `setup()` / `get_dm_spam_detector()` lifecycle
- `detector.py` - Core spam detection engine with atomic window counters

## Key Classes

### `DMSpamDetector`

Main spam detection service with the following checks:

1. **Rate limiting** - Per-sender/recipient message rate tracking with configurable windows
2. **Duplicate detection** - Identical content hash tracking within a time window, scoped per (sender, conversation) so cross-chat repetition of benign text is not flagged
3. **Recipient filters** - `rate`, `duplicate`, and `pattern` (fnmatch glob) filter types with `block` vs `warn` actions; global and per-sender targeted filters are matched with proper NULL semantics
4. **Pattern heuristics** - Case-insensitive URL parsing, whitespace-split-evasion-proof mention counting, invite link markers (`discord.gg/`, `discord.com/invite/`, `t.me/`)

### Configuration

- `DEFAULT_RATE_LIMIT` - Max messages per window (default: 10)
- `DEFAULT_RATE_WINDOW` - Window in seconds (default: 60)
- `DEFAULT_DUPLICATE_THRESHOLD` - Max identical messages (default: 3)
- `DEFAULT_DUPLICATE_WINDOW` - Window in seconds (default: 300)
- `DEFAULT_NEW_USER_RATE_LIMIT` - Stricter limit for new accounts (default: 5)
- `DEFAULT_NEW_USER_AGE_MS` - Account age threshold for "new user" (default: 7 days)

### Counter Storage (multi-worker semantics)

Rate and duplicate counters use an atomic record-and-count primitive:

- **Valkey available**: an atomic `INCR` + first-hit `EXPIRE` Lua script - limits hold exactly across all worker processes.
- **Valkey unavailable**: a process-local lock-protected dict - enforcement degrades to per-worker limits. The degradation is logged once as a WARNING; detection fails open if both tiers fail.

Blocked attempts are always recorded inside their window, so probing at the limit keeps applying pressure.

### Usage

```python
from src.core.antispam import get_dm_spam_detector

detector = get_dm_spam_detector()
if detector is not None:
    allowed, reason = detector.check_message(sender_id, recipient_id, content)
    if not allowed:
        # Block the message, return error to user
        pass
```
