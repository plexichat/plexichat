# DM anti-spam

Protect direct message (DM) conversations from harassment and spam with rate limiting, duplicate detection, and pattern filtering.

## Overview

The DM anti-spam module inspects every DM sent through `POST /channels/{id}/messages` before the message is persisted. The route calls `_enforce_dm_spam_protection` in `src/api/routes/messages/messages_crud/base.py`, which delegates to `DMSpamDetector` in `detector.py:114`. The detector is initialized during server bootstrap alongside AutoMod through `setup()` in `__init__.py:14`.

The detector fails open. If detection raises an exception, `check_message` in `detector.py:240` logs a warning and allows the message.

## How it works

`DMSpamDetector.check_message` in `detector.py:264` evaluates messages in the following order. Each step records the attempt before making a decision, so blocked attempts continue to apply pressure within the window.

1. **Rate limiting** – The detector counts messages per `(sender_id, recipient_id)` pair within a fixed window by using `AtomicWindowCounter.record_and_count` in `detector.py:74`. If the count exceeds the effective limit, the detector applies the recipient's `rate` filter action (`block` or `warn`).
2. **Duplicate detection** – The detector hashes the message with SHA-256 (first 16 hex characters) and counts identical hashes per `(sender_id, recipient_id, content_hash)` within a fixed window. This scope prevents benign text that is repeated across different conversations from accumulating toward the limit.
3. **Recipient pattern filters** – The detector evaluates recipient-defined `pattern` filters that use `fnmatch` glob syntax. Matching is case-insensitive and deterministic across platforms because it uses `fnmatch.fnmatchcase` on lowered strings in `detector.py:347`.
4. **Built-in heuristics** – The detector scores heuristic indicators in `detector.py:376`: URL count (case-insensitive `https?://` parsing in `detector.py:126`), mention count with whitespace-split evasion collapsing (`<\s*@\s*` in `detector.py:127`), and invite markers (`discord.gg/`, `discord.com/invite/`, `t.me/` in `detector.py:128`). A score of 3 or more blocks the message.

## Filter types

The recipient configures filters in `dm_spam_filters`. `get_user_filters` in `detector.py:162` returns enabled filters ordered by `created_at` and `id`, and `_get_user_filters` includes both global filters (`target_user_id IS NULL`) and per-sender targeted filters.

| Filter type | Description | Action values | Evaluated |
|---|---|---|---|
| `rate` | Maximum messages per window for the sender | `block` (default), `warn` | First matching filter determines the threshold and action. Defaults apply when no filter exists. |
| `duplicate` | Maximum identical messages per window | `block` (default), `warn` | First matching filter determines the threshold and action. |
| `pattern` | `fnmatch` glob against the full message content | `block` (default), `warn` | Filters are evaluated in deterministic order; the first match decides. |

When the action is `warn`, `_enforce` in `detector.py:216` records the event as `*_warned` and allows the message. Any other action (including the default `block`) rejects the message and returns the reason.

## Counter storage

Rate and duplicate counters use an atomic record-and-count primitive that is defined in `detector.py:32`:

* **Valkey available** – The detector runs an `INCR` plus first-hit `EXPIRE` Lua script on Valkey. The operation is atomic on the server, so limits hold exactly across all worker processes.
* **Valkey unavailable** – The detector falls back to a process-local dictionary that is protected by `threading.Lock` in `detector.py:94`. This enforces limits per worker only. The degradation is logged once as a warning through `_warn_valkey_down` in `detector.py:52`. The local store caps at 50,000 keys and evicts the oldest half when full.

## Configuration

| Constant | Default | Description |
|---|---|---|
| `DEFAULT_RATE_LIMIT` | `10` | Maximum messages per window for established users. |
| `DEFAULT_RATE_WINDOW` | `60` seconds | Fixed window for rate limiting. |
| `DEFAULT_DUPLICATE_THRESHOLD` | `3` | Maximum identical messages per window. |
| `DEFAULT_DUPLICATE_WINDOW` | `300` seconds (5 minutes) | Fixed window for duplicate detection. |
| `DEFAULT_NEW_USER_RATE_LIMIT` | `5` | Stricter rate limit applied to new accounts. The effective limit is `min(rate_limit, 5)`. |
| `DEFAULT_NEW_USER_AGE_MS` | `7` days | Account age threshold. Accounts younger than this value are considered new. |

Recipient overrides are stored per filter in `threshold` and `window_seconds`. You can set overrides through `set_filter` in `detector.py:430`.

## Set up the module

Initialize the detector once during server startup:

```python
from src.core.antispam import setup

setup(db, auth_module)
```

To retrieve the detector elsewhere, use `get_dm_spam_detector` in `__init__.py:20`:

```python
from src.core.antispam import get_dm_spam_detector

detector = get_dm_spam_detector()
if detector is not None:
    allowed, reason = detector.check_message(sender_id, recipient_id, content)
    if not allowed:
        # Return an error to the sender
        pass
```

If `setup()` was not called, `get_dm_spam_detector` lazily builds a detector from the API database or returns `None`.

For tests, call `reset_detector` to clear the singleton and the process-local windows in `__init__.py:35`.

## Use the module

### Check a message

```python
from src.core.antispam import get_dm_spam_detector

detector = get_dm_spam_detector()
allowed, reason = detector.check_message(sender_id, recipient_id, "Hello!")
```

The method returns `(True, None)` when the message is allowed and `(False, reason)` when it is blocked.

### Manage filters

```python
# Create or update a filter (global or per-sender)
detector.set_filter(
    user_id=recipient_id,
    filter_type="pattern",
    action="block",
    pattern="*spam*",
    target_user_id=None,  # None for global; set to sender_id for targeted
)

# List filters
filters = detector.get_filters(recipient_id)

# Remove a filter
detector.remove_filter(recipient_id, filter_id)
```

`set_filter` in `detector.py:430` performs a transactional upsert with proper `NULL` semantics for `target_user_id`, so updating a global filter never creates a duplicate row.

### View statistics

```python
stats = detector.get_spam_stats(recipient_id)
# Returns events by type and top senders for the last 24 hours
```

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Module exports and lifecycle helpers (`setup`, `get_dm_spam_detector`, `reset_detector`). |
| `detector.py` | Core detection engine, `AtomicWindowCounter`, and filter management. |

## Limitations

* Counters are per worker when Valkey is unavailable. A sender can exceed the intended limit by distributing messages across workers.
* The heuristic score is conservative (blocks at `>= 3`) and does not inspect attachments.
* Pattern filters use `fnmatch` globs, not regular expressions.
