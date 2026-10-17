# Rate Limit Tests

These tests verify the rate-limiting module, including bucket management,
limit enforcement, header generation, storage backends, middleware integration,
and time-window behavior.

## Test files

- `test_buckets.py` -- Create rate-limit buckets, generate bucket keys and
  IDs, and calculate effective limits with burst and multiplier settings.
- `test_bypass.py` -- Bypass rate limits for admin users, internal requests,
  and custom bypass functions.
- `test_edge_cases.py` -- Handle concurrent requests, null users, zero and
  negative values, very short and long time windows, snowflake IDs, and
  special route patterns.
- `test_headers.py` -- Generate `X-RateLimit-*` headers, `Retry-After`, and
  response body for 429 responses.
- `test_limits.py` -- Enforce global, per-user, per-route, per-resource,
  hourly, and daily rate limits, and support cost-weighted requests.
- `test_middleware.py` -- Integrate with FastAPI middleware, extract route
  patterns, and apply bypass rules at the HTTP layer.
- `test_routes.py` -- Configure per-route limits, merge route configs, apply
  bot and webhook multipliers, and enforce scope-specific buckets.
- `test_storage.py` -- Perform basic storage operations, manage TTL
  expiration, run prefix-based queries, execute atomic operations, handle
  concurrent access, and verify thread safety.
- `test_windows.py` -- Reset fixed windows, apply sliding-window recovery,
  refill token buckets, drain leaky buckets, and track hourly and daily
  window resets.
