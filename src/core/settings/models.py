"""
User Settings data models.
"""

from dataclasses import dataclass
from src.core.base import SnowflakeID


@dataclass
class SettingsConfig:
    """Configuration for the settings module."""

    max_settings_per_user: int = 100
    max_key_length: int = 100
    max_value_length: int = 10000
    reserved_keys: tuple = ("__internal",)
    # Key namespaces reserved for server semantics. Single/double underscore
    # prefixes are never user-writable through the generic endpoints; server
    # keys (e.g. ``_focused_channel``) persist via dedicated paths only.
    reserved_prefixes: tuple = ("__internal", "_")
    # Keys inside the single-underscore namespace that the server itself
    # persists (via their dedicated route handling).
    server_setting_keys: tuple = ("_focused_channel",)
    # Key charset contract: [a-z0-9_-], case-insensitive.
    key_charset: str = "A-Za-z0-9_-"
    # Bulk write caps.
    max_bulk_entries: int = 50
    max_bulk_payload_bytes: int = 100000


@dataclass
class UserSetting:
    """A single user setting."""

    id: SnowflakeID
    user_id: SnowflakeID
    key: str
    value: str
    created_at: int
    updated_at: int
