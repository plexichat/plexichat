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
import os as _os
from typing import Any, Optional, Dict
from .core import ConfigLoader, MalformedConfigAction

# Global config instance
_config_instance: Optional[ConfigLoader] = None
_setup_called = False

# Security allowlists whose explicit operator value must win over the code
# defaults. ConfigLoader._deep_merge concatenates string lists (so a partial
# list inherits defaults); for an allowlist that is a fail-open widening - an
# operator who narrows the list silently gets the broader defaults back. The
# keys below are restored verbatim from the raw config file after merge.
_EXPLICIT_ALLOWLIST_PATHS = (
    ("api", "cors_origins"),
    # CORS method/header gates are allowlists in the same family as origins.
    # Narrowing them (e.g. read-only methods, dropping X-RateLimit-Bypass) is a
    # hardening measure; the deep merge must not re-add the broad defaults and
    # silently re-enable cross-origin state changes or privileged headers.
    ("api", "cors_allow_methods"),
    ("api", "cors_allow_headers"),
    # URL-preview scheme gate (SSRF): an operator narrowing to https-only must
    # not have http silently re-added by the deep merge.
    ("embeds", "url_preview", "allowed_schemes"),
)


def _apply_explicit_allowlists(instance: ConfigLoader) -> None:
    """Restore explicit security allowlist values from the raw config file.

    ``config.get("api")`` is post-deep-merge and therefore always contains the
    broader code defaults for list-valued allowlists such as ``cors_origins``.
    Reading the operator's own file lets an explicit (possibly empty) allowlist
    take effect exactly as written, instead of being silently widened. When the
    key is absent from the file, the merged defaults are left untouched.
    """
    path = getattr(instance, "config_path", None)
    if not path or not _os.path.exists(path):
        return
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if str(path).lower().endswith(".json"):
            import json as _json

            doc = _json.loads(raw) if raw else {}
        else:
            import yaml as _yaml

            doc = _yaml.safe_load(raw) if raw else {}
        if not isinstance(doc, dict):
            return
        merged = getattr(instance, "config", None)
        if not isinstance(merged, dict):
            return

        # Keep environment interpolation working for explicit origins, exactly
        # as ConfigLoader would have resolved them during the merge.
        resolve = getattr(instance, "_resolve_value", None)
        changed = False
        for keys in _EXPLICIT_ALLOWLIST_PATHS:
            node: Any = doc
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    node = None
                    break
                node = node[key]
            if not isinstance(node, list):
                continue
            explicit = [
                resolve(item) if isinstance(item, str) and callable(resolve) else item
                for item in node
            ]
            parent: Any = merged
            for key in keys[:-1]:
                if not isinstance(parent, dict):
                    parent = None
                    break
                parent = parent.get(key)
            if isinstance(parent, dict) and keys[-1] in parent:
                parent[keys[-1]] = explicit
                changed = True
        if changed:
            invalidate = getattr(instance, "_invalidate_cache", None)
            if callable(invalidate):
                invalidate()
    except Exception:
        # A failure to re-read must not change the already-loaded config.
        return


def setup(
    config_path: str,
    default_config: Optional[Dict[str, Any]] = None,
    malformed_action: str = "CRASH_ON_SINGLE",
) -> None:
    """
    Setup the config loader. Should be called once in your main application file.

    Args:
        config_path (str): Path to the config file.
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
    _apply_explicit_allowlists(_config_instance)
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
