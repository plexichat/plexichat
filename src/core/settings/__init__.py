"""
User Settings module - Cloud-synced key-value store for user preferences.

Provides a simple key-value store for user settings like themes, UI preferences,
and other client-side configurations that should sync across devices.

Usage:
    # In main.py (setup once)
    from src.core.settings import setup as settings_setup
    settings_setup(db)
    
    # In any other file
    from src.core.settings import get_setting, set_setting, get_all_settings
    
    # Get a setting
    theme = get_setting(user_id, "theme")
    
    # Set a setting
    set_setting(user_id, "theme", "dark")
    
    # Get all settings
    all_settings = get_all_settings(user_id)
"""

from typing import Optional, Dict, Any, List
from .manager import SettingsManager
from .models import UserSetting, SettingsConfig

__all__ = [
    "setup",
    "get_setting",
    "set_setting",
    "delete_setting",
    "get_all_settings",
    "UserSetting",
    "SettingsConfig",
]

_manager: Optional[SettingsManager] = None
_setup_complete = False


def setup(db, config: Optional[SettingsConfig] = None) -> None:
    """
    Initialize the settings module.
    
    Args:
        db: Database instance (must be connected)
        config: Optional settings configuration
    """
    global _manager, _setup_complete
    _manager = SettingsManager(db, config)
    _setup_complete = True


def _ensure_setup():
    """Ensure setup was called."""
    if not _setup_complete:
        raise RuntimeError("Settings module not initialized. Call settings.setup() first.")


def get_setting(user_id: int, key: str) -> Optional[str]:
    """Get a single setting value for a user."""
    _ensure_setup()
    assert _manager is not None
    return _manager.get(user_id, key)


def set_setting(user_id: int, key: str, value: str) -> UserSetting:
    """Set a setting value for a user."""
    _ensure_setup()
    assert _manager is not None
    return _manager.set(user_id, key, value)


def delete_setting(user_id: int, key: str) -> bool:
    """Delete a setting for a user."""
    _ensure_setup()
    assert _manager is not None
    return _manager.delete(user_id, key)


def get_all_settings(user_id: int) -> Dict[str, str]:
    """Get all settings for a user as a dictionary."""
    _ensure_setup()
    assert _manager is not None
    return _manager.get_all(user_id)


def get_settings_list(user_id: int) -> List[UserSetting]:
    """Get all settings for a user as a list of UserSetting objects."""
    _ensure_setup()
    assert _manager is not None
    return _manager.get_all_as_list(user_id)


def get_settings_count(user_id: int) -> int:
    """Get the count of settings for a user."""
    _ensure_setup()
    assert _manager is not None
    return _manager.get_count(user_id)


def is_setup() -> bool:
    """Check if settings module is initialized."""
    return _setup_complete
