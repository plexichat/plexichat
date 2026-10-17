# User features module

The user features module manages feature flags, profile badges, and rate-limit tiers for PlexiChat users. Only admins can grant or revoke features; users cannot modify their own features.

## Overview

The module provides three capabilities:

- **Feature flags** — Boolean flags that enable capabilities such as voice access.
- **Profile badges** — Visual badges displayed on user profiles, such as `alpha_tester` or `staff`.
- **Rate-limit tiers** — Named tiers (`standard`, `alpha`, `premium`) that control limits for voice minutes, file uploads, server count, and more.

Features are stored in dedicated tables (`user_features`, `user_feature_usage`, `user_features_audit`) separate from `auth_users` so you can query feature status and audit admin changes independently. See `schema.py:9` and `models.py:1`.

## Before you begin

Initialize the module with a database handle before you call any other function.

```python
from src.core import features

features.setup(db)
```

If you call a feature function before setup, it raises `RuntimeError`. Use `features.is_setup()` to check initialization state.

## Configure tiers and badges

Configure the module through `config.yaml` under the `user_features` key. The module in `__init__.py:116` reads this configuration at runtime.

```yaml
user_features:
  alpha_registration_enabled: false
  default_tier: standard
  badge_display_limit: 5
  available_badges:
    - alpha_tester
    - early_supporter
    - staff
    - verified
    - bug_hunter
    - contributor
  rate_limit_tiers:
    standard:
      multiplier: 1.0
      max_voice_minutes_per_day: 120
      max_video_minutes_per_day: 60
      max_file_uploads_per_day: 50
      max_file_size_mb: 10
      max_servers: 100
    alpha:
      multiplier: 2.0
      max_voice_minutes_per_day: 480
      max_video_minutes_per_day: 240
      max_file_uploads_per_day: 200
      max_file_size_mb: 25
      max_servers: 200
    premium:
      multiplier: 3.0
      max_voice_minutes_per_day: -1  # -1 means unlimited
      max_video_minutes_per_day: -1
      max_file_uploads_per_day: 500
      max_file_size_mb: 100
      max_servers: 500
```

Calling `features.get_tier_limits(tier)` in `__init__.py:140` returns a `TierLimits` object defined in `models.py:106`. If the tier is not in the config, the function falls back to `DEFAULT_TIER_LIMITS` in `models.py:150` or raises `InvalidTierError`.

## Use alpha registration mode

When `alpha_registration_enabled` is `true`, every new registration automatically receives the `alpha` tier and the `alpha_tester` badge. This grant is permanent for those users; disabling the flag affects only future registrations.

```python
from src.core import features

# Check whether alpha registration is active
if features.is_alpha_registration_enabled():
    print("New users will get the alpha tier.")

# Apply the grant manually (called automatically on registration)
features.apply_new_user_features(user_id)
```

`apply_new_user_features()` is defined in `__init__.py:193`. It inserts a row into `user_features` with `rate_limit_tier = "alpha"` and `badges = ["alpha_tester"]`. If the flag is disabled, it returns `None`.

## Get tier and badge information

```python
from src.core import features

# Get the user's current tier
tier = features.get_user_tier(user_id)  # "standard", "alpha", or "premium"

# Get limits for a specific tier
limits = features.get_tier_limits(tier)
print(limits.max_file_size_mb)

# Get limits for the user's current tier
user_limits = features.get_user_tier_limits(user_id)

# Get the rate-limit multiplier for middleware
multiplier = features.get_rate_limit_multiplier(user_id)  # 1.0, 2.0, or 3.0
effective_limit = int(base_limit * multiplier)

# Get the user's badges (truncated to badge_display_limit)
badges = features.get_user_badges(user_id)  # ["alpha_tester", "verified"]

# Get all stored features for the user
user_features = features.get_user_features(user_id)

# Check a feature flag (checks expiration first)
if features.has_feature(user_id, "can_voice"):
    allow_voice_access()
```

Key functions:

- `get_user_tier(user_id)` in `__init__.py:288` — Returns the default tier if no row exists or if the grant has expired.
- `get_user_tier_limits(user_id)` in `__init__.py:307` — Convenience wrapper around `get_user_tier()` and `get_tier_limits()`.
- `get_rate_limit_multiplier(user_id)` in `__init__.py:571` — Used by rate-limiting middleware.
- `get_user_badges(user_id)` in `__init__.py:343` — Returns at most `badge_display_limit` badges.
- `has_feature(user_id, feature)` in `__init__.py:321` — Returns `False` if the grant has expired.
- `get_available_tiers()` in `__init__.py:132` — Returns tier names from config or `["standard", "alpha", "premium"]`.

## Manage features as an admin

All write operations require an `admin_id` for auditing.

```python
from src.core import features

# Set or update tier and metadata
features.set_user_features(
    user_id=123,
    admin_id=1,
    rate_limit_tier="premium",
    expires_at=1735689600,
    notes="Premium trial for 30 days",
)

# Set tier directly
features.set_user_tier(user_id, admin_id, "alpha", expires_at=None)

# Add a badge
features.add_badge(user_id, admin_id, "alpha_tester")

# Remove a badge
features.remove_badge(user_id, admin_id, "alpha_tester")
```

- `set_user_features()` in `__init__.py:365` — Creates or updates the `user_features` row. Raises `InvalidTierError` for unknown tiers.
- `set_user_tier()` in `__init__.py:552` — Wrapper around `set_user_features()` that sets only the tier.
- `add_badge()` in `__init__.py:459` — Validates against `available_badges` and raises `InvalidBadgeError` for unknown badges. Invalidates the `user_profile:{user_id}` cache if Valkey is available.
- `remove_badge()` in `__init__.py:511` — Removes the badge if present and invalidates the cache.

## Handle expiration

You can set `expires_at` as a Unix timestamp in milliseconds. When a grant has expired:

- `get_user_tier()` returns the default tier.
- `has_feature()` returns `False`.
- Badges remain visible; they do not expire.

To check expiration manually:

```python
import time

user_features = features.get_user_features(user_id)
if user_features and user_features.expires_at and user_features.expires_at < int(time.time() * 1000):
    print("Features have expired.")
```

## Badges

Badges are defined in `models.py:11` as `AVAILABLE_BADGES`. Use `Badge.get_badge_info(name)` in `models.py:34` to get display metadata.

| Badge | Display name | Description |
| --- | --- | --- |
| `alpha_tester` | Alpha Tester | Participated in alpha testing |
| `early_supporter` | Early Supporter | Supported PlexiChat early |
| `staff` | Staff | PlexiChat team member |
| `verified` | Verified | Verified account |
| `bug_hunter` | Bug Hunter | Found and reported bugs |
| `contributor` | Contributor | Contributed code or docs |
| `moderator` | Moderator | Community moderator |
| `partner` | Partner | Partner program member |

## Database schema

The schema is defined in `schema.py:9`.

```sql
-- Main features table
CREATE TABLE user_features (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    rate_limit_tier TEXT DEFAULT 'standard',
    badges TEXT DEFAULT '[]',
    granted_by INTEGER,
    granted_at INTEGER,
    expires_at INTEGER,
    notes TEXT
);

-- Usage tracking for per-day limits
CREATE TABLE user_feature_usage (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    usage_type TEXT NOT NULL,
    usage_date TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0,
    usage_value INTEGER DEFAULT 0,
    updated_at INTEGER NOT NULL
);

-- Audit log for admin changes
CREATE TABLE user_features_audit (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at INTEGER NOT NULL
);
```

## Rate-limit integration

Use `get_rate_limit_multiplier()` to scale limits in middleware:

```python
from src.core import features

base_limit = 100  # requests per minute
multiplier = features.get_rate_limit_multiplier(user_id)
effective_limit = int(base_limit * multiplier)
```
