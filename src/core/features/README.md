# User Features Module

Manages user feature flags, profile badges, and rate limit tiers.

## Overview

This module provides a secure, admin-controlled system for:
- **Feature Flags**: Boolean flags like `can_create_org` that enable specific capabilities
- **Profile Badges**: Visual badges displayed on user profiles (alpha_tester, staff, etc.)
- **Rate Limit Tiers**: Configurable tiers (standard, alpha, premium) with specific limits

Users cannot modify their own features - only admins can grant/revoke features.

## Configuration

In `config.yaml` or `main.py` default config:

```yaml
user_features:
  default_tier: standard
  badge_display_limit: 5
  available_badges:
    - alpha_tester
    - early_supporter
    - staff
    - org_root
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
      max_voice_minutes_per_day: -1  # unlimited
      max_video_minutes_per_day: -1
      max_file_uploads_per_day: 500
      max_file_size_mb: 100
      max_servers: 500
```

## Usage

```python
from src.core import features

# Get user's tier
tier = features.get_user_tier(user_id)  # Returns 'standard', 'alpha', etc.

# Get tier limits
limits = features.get_tier_limits(tier)
print(limits.max_file_size_mb)  # 10 for standard

# Get user's specific limits
user_limits = features.get_user_tier_limits(user_id)

# Check feature flag
if features.has_feature(user_id, "can_create_org"):
    # Allow org creation
    pass

# Get user badges
badges = features.get_user_badges(user_id)  # ['alpha_tester', 'verified']

# Get rate limit multiplier (for rate limiting middleware)
multiplier = features.get_rate_limit_multiplier(user_id)  # 2.0 for alpha
```

## Admin Functions

```python
# Set user tier (admin only)
features.set_user_tier(user_id, admin_id, "alpha", expires_at=None)

# Add badge
features.add_badge(user_id, admin_id, "alpha_tester")

# Remove badge
features.remove_badge(user_id, admin_id, "alpha_tester")

# Set multiple features at once
features.set_user_features(
    user_id=123,
    admin_id=1,
    can_create_org=True,
    rate_limit_tier="premium",
    expires_at=1735689600,  # Unix timestamp
    notes="Premium trial for 30 days"
)
```

## Database Schema

```sql
-- Main features table
CREATE TABLE user_features (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    can_create_org INTEGER DEFAULT 0,
    rate_limit_tier TEXT DEFAULT 'standard',
    badges TEXT DEFAULT '[]',  -- JSON array
    granted_by INTEGER,
    granted_at INTEGER,
    expires_at INTEGER,
    notes TEXT
);

-- Usage tracking (for limits)
CREATE TABLE user_feature_usage (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    usage_type TEXT NOT NULL,
    usage_date TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0,
    usage_value INTEGER DEFAULT 0,
    updated_at INTEGER NOT NULL
);

-- Audit log
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

## Available Badges

| Badge | Description |
|-------|-------------|
| `alpha_tester` | Alpha testing participant |
| `early_supporter` | Early adopter |
| `staff` | PlexiChat team member |
| `org_root` | Organization administrator |
| `verified` | Verified account |
| `bug_hunter` | Found and reported bugs |
| `contributor` | Code/docs contributor |
| `moderator` | Community moderator |
| `partner` | Partner program member |

## Rate Limit Integration

The rate limiting middleware uses `get_rate_limit_multiplier()` to adjust limits:

```python
# In rate limit middleware
from src.core import features

base_limit = 100  # requests per minute
multiplier = features.get_rate_limit_multiplier(user_id)
effective_limit = int(base_limit * multiplier)
```

## Expiration

Features can have an expiration timestamp. When expired:
- `get_user_tier()` returns the default tier
- `has_feature()` returns False
- Badges remain visible (they don't expire)

Check expiration manually:
```python
user_features = features.get_user_features(user_id)
if user_features.expires_at and user_features.expires_at < time.time():
    # Features have expired
    pass
```
