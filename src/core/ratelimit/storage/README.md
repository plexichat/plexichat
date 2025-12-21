# Rate Limit Storage

Storage backends for rate limit state.

## Backends

- `memory.py` - In-memory storage (single instance)
- `base.py` - Abstract base class

## Usage

```python
from src.core.ratelimit.storage import MemoryStorage

storage = MemoryStorage()
storage.increment(key, window_seconds)
count = storage.get_count(key)
```

## Interface

All backends implement `RateLimitStorage` with methods for incrementing counters and checking limits.
