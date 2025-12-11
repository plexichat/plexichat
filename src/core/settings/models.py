"""
User Settings data models.
"""

from dataclasses import dataclass


@dataclass
class SettingsConfig:
    """Configuration for the settings module."""
    max_settings_per_user: int = 100
    max_key_length: int = 100
    max_value_length: int = 10000
    reserved_keys: tuple = ("__internal",)


@dataclass
class UserSetting:
    """A single user setting."""
    id: int
    user_id: int
    key: str
    value: str
    created_at: int
    updated_at: int
