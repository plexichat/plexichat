"""
Config utility module - Zero-friction configuration management for Python applications.

Usage:
    # In main.py (setup once)
    import utils.config as config
    config.setup(config_path="config.yaml")

    # In any other file (no setup needed)
    import utils.config as config
    db_host = config.get("db_host")
    config.set("db_port", 5432)
"""

import copy as _copy
from typing import Any, Optional, Dict
from .core import ConfigLoader, MalformedConfigAction

# Global config instance
_config_instance: Optional[ConfigLoader] = None
_setup_called = False


def setup(
    config_path: str,
    default_config: Optional[Dict[str, Any]] = None,
    malformed_action: str = "CRASH_ON_SINGLE",
) -> None:
    """
    Setup the config loader. Should be called once in your main application file.

    Args:
        config_path (str): Path to the config file (YAML or JSON).
        default_config (dict): Default configuration to create if file doesn't exist.
        malformed_action (str): Action on malformed config - "CRASH_ON_SINGLE", "CRASH_ON_MANY", or "IGNORE".
    """
    global _config_instance, _setup_called

    # Convert string to enum
    action_map = {
        "CRASH_ON_SINGLE": MalformedConfigAction.CRASH_ON_SINGLE,
        "CRASH_ON_MANY": MalformedConfigAction.CRASH_ON_MANY,
        "IGNORE": MalformedConfigAction.IGNORE,
    }
    action = action_map.get(
        malformed_action.upper(), MalformedConfigAction.CRASH_ON_SINGLE
    )

    _config_instance = ConfigLoader(
        config_path=config_path, default_config=default_config, malformed_action=action
    )
    _setup_called = True


def _ensure_setup() -> None:
    """Internal: Ensures setup was called before using config functions."""
    if not _setup_called:
        raise RuntimeError(
            "Config not configured. Please call config.setup() in your main.py file first."
        )


def get(key: str, default: Any = None) -> Any:
    """
    Get a configuration value.

    Args:
        key (str): Configuration key to retrieve.
        default: Default value if key doesn't exist.

    Returns:
        The configuration value or default.
    """
    _ensure_setup()
    assert _config_instance is not None
    return _config_instance.get(key, default)


def set(key: str, value: Any) -> None:
    """
    Set a configuration value and save to file.

    Args:
        key (str): Configuration key to set.
        value: Value to set.
    """
    _ensure_setup()
    assert _config_instance is not None
    _config_instance.set(key, value)


def get_all() -> Dict[str, Any]:
    """
    Get all configuration values as a deep copy.

    A deep copy is returned so callers can safely mutate the result
    (e.g. before passing it to a templating engine) without affecting
    the live config dict held by :class:`ConfigLoader`.

    Returns:
        Dictionary of all configuration values.
    """
    _ensure_setup()
    assert _config_instance is not None
    return _copy.deepcopy(_config_instance.config)


# For backward compatibility, also expose the ConfigLoader class and enum
__all__ = ["ConfigLoader", "MalformedConfigAction", "setup", "get", "set", "get_all"]
