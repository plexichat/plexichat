# User Profiles Module

Custom status, bio, social links, and profile management for Plexichat users.

## Files

- `__init__.py` - Module exports (`ProfileManager`)
- `manager.py` - Core profile business logic

## Key Classes

### `ProfileManager`

Handles all profile CRUD operations with validation:

- **Bio** - Up to 1000 characters
- **Custom status** - Text (128 chars) + emoji (32 chars) with optional expiration
- **Social links** - Up to 10 links from allowed platforms (github, twitter, linkedin, website, youtube, twitch, discord, reddit, mastodon, other)
- **Pronouns** - Up to 40 characters
- **Location** - Up to 100 characters
- **Timezone** - Up to 64 characters
- **Banner image** - URL to banner image

### Usage

```python
from src.core.profiles import ProfileManager

mgr = ProfileManager(db, auth_module)

# Get a user's profile
profile = mgr.get_profile(user_id)

# Update profile fields
profile = mgr.update_profile(
    user_id=123,
    bio="Hello, I'm a developer!",
    status="Working",
    status_emoji="💻",
    social_links=[{"platform": "github", "url": "https://github.com/user"}],
)
```
