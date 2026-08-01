# User Settings

Cloud-synced key-value store for user preferences.

## Features

- Simple key-value storage for user settings
- Cross-device synchronization
- Supports themes, UI preferences, and client configurations

## Usage

```python
from src.core.settings import setup, get_setting, set_setting

# Initialize
setup(db)

# Get/set settings
theme = get_setting(user_id, "theme")
set_setting(user_id, "theme", "dark")
```

## Files

- `manager.py` - SettingsManager class
- `models.py` - UserSetting and SettingsConfig models
- `schema.py` - Pydantic schemas
- `exceptions.py` - Custom exceptions
