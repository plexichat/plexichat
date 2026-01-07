"""
Exceptions for the settings module.
"""


class SettingsError(Exception):
    """Base exception for settings errors."""

    pass


class SettingsLimitExceeded(SettingsError):
    """Raised when user exceeds maximum settings limit."""

    pass


class SettingsKeyTooLong(SettingsError):
    """Raised when setting key exceeds maximum length."""

    pass


class SettingsValueTooLong(SettingsError):
    """Raised when setting value exceeds maximum length."""

    pass


class SettingsKeyReserved(SettingsError):
    """Raised when trying to use a reserved key."""

    pass


class SettingsNotFound(SettingsError):
    """Raised when a setting is not found."""

    pass
