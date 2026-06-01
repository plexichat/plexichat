import os
import json
import yaml
import logging
import re
from typing import Any, Dict, Optional
from enum import Enum

# Standard logger for internal config errors
logger = logging.getLogger(__name__)

# Regex for environment variable interpolation: ${VAR} or ${VAR:-default}
INTERPOLATION_RE = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")

# Sentinel used to distinguish a cached None from a cache miss in get().
_CACHE_SENTINEL = object()


class MalformedConfigAction(Enum):
    CRASH_ON_SINGLE = "crash_on_single"
    CRASH_ON_MANY = "crash_on_many"
    IGNORE = "ignore"


class ConfigLoader:
    """
    A simple, robust configuration loader supporting YAML and JSON
    with environment variable interpolation.
    """

    def __init__(
        self,
        config_path: str,
        default_config: Optional[Dict[str, Any]] = None,
        malformed_action: MalformedConfigAction = MalformedConfigAction.CRASH_ON_SINGLE,
    ):
        """
        Initialize the ConfigLoader.

        Args:
            config_path (str): Path to the config file.
            default_config (dict): Default configuration to write if file doesn't exist.
            malformed_action (MalformedConfigAction): Action to take on malformed config.
        """
        self.config_path = config_path
        self.default_config = default_config or {}
        self.malformed_action = malformed_action
        self.config: Dict[str, Any] = {}

        # Hot-path cache for get(). Invalidated on set() / reload.
        self._get_cache: Dict[Any, Any] = {}

        self._load_config()

    def _resolve_value(self, value: str) -> str:
        """
        Resolve environment variable interpolation in a string value.

        Supports:
        - ${VAR} - Required variable, raises error if not set
        - ${VAR:-default} - Optional variable, uses default if not set

        Args:
            value: String value that may contain interpolation patterns

        Returns:
            Resolved string value

        Raises:
            ValueError: If required environment variable is not set
        """
        if not isinstance(value, str):
            return value

        # Fast path: no interpolation possible, skip the regex entirely.
        # This is a common case (most config strings are plain) and the
        # regex sub + replacer closure add measurable overhead.
        if "$" not in value:
            return value

        def replacer(match):
            var_name = match.group(1)
            default = match.group(2)
            val = os.environ.get(var_name)

            if val is None:
                if default is not None:
                    logger.debug(f"Using default for environment variable: {var_name}")
                    return default
                raise ValueError(
                    f"Required environment variable '{var_name}' is not set. "
                    f"Please set it before starting the application."
                )

            logger.info(f"Resolved environment variable: {var_name}")
            return val

        return INTERPOLATION_RE.sub(replacer, value)

    def _coerce_value(self, value: str, default_value: Any = None) -> Any:
        """
        Coerce a resolved string value to the type of the corresponding default value.

        This ONLY applies type coercion when the default config provides a type hint.
        Bare string values without a typed default are left as strings to prevent
        silent data corruption (e.g. port "5432" staying as int is fine because the
        default is already int, but a password "12345" must NOT become int 12345).

        Args:
            value: Resolved string value to coerce
            default_value: Corresponding value from default config for type inference

        Returns:
            Coerced value matching the default's type, or the original string
        """
        if default_value is not None and not isinstance(default_value, str):
            # Default is typed — coerce the resolved value to match
            if isinstance(default_value, bool):
                lower_val = value.lower()
                if lower_val in ("true", "1", "yes", "on"):
                    return True
                elif lower_val in ("false", "0", "no", "off"):
                    return False
                # Fall through — couldn't parse as bool
            elif isinstance(default_value, int):
                try:
                    return int(value)
                except (ValueError, TypeError):
                    pass
            elif isinstance(default_value, float):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    pass
            # For other non-string types, just return the string
            # (the consumer will handle type conversion)
        # No typed default, or default is also a string — keep as string
        return value

    def _resolve_config(self, node: Any, default_node: Any = None) -> Any:
        """
        Recursively resolve environment variable interpolation in a config tree.

        Type coercion is ONLY applied to values that underwent interpolation
        (contained ${...} patterns) AND have a typed default value to infer from.
        Non-interpolated string values are always preserved as-is.

        Args:
            node: Config node (dict, list, or primitive value)
            default_node: Corresponding node from default config for type inference

        Returns:
            Resolved config node with all interpolations resolved and types coerced
            only where the default config provides a type hint.
        """
        if isinstance(node, dict):
            default_dict = default_node if isinstance(default_node, dict) else {}
            return {
                k: self._resolve_config(v, default_dict.get(k)) for k, v in node.items()
            }
        elif isinstance(node, list):
            default_list = default_node if isinstance(default_node, list) else []
            return [
                self._resolve_config(
                    item, default_list[i] if i < len(default_list) else None
                )
                for i, item in enumerate(node)
            ]
        else:
            # Fast path: plain string with no interpolation tokens — no
            # regex, no coercion, no recursion. _resolve_value would also
            # short-circuit, but we save the call entirely.
            if isinstance(node, str) and "$" not in node:
                return node
            resolved_value = self._resolve_value(node)
            # Only apply type coercion to values that were actually interpolated
            # AND have a typed default to infer the correct type from.
            # This prevents "0" becoming False or passwords becoming ints.
            if (
                isinstance(node, str)
                and INTERPOLATION_RE.search(node)
                and isinstance(resolved_value, str)
            ):
                resolved_value = self._coerce_value(resolved_value, default_node)
            return resolved_value

    def _load_config(self) -> None:
        """Loads the configuration from the file."""
        used_defaults = False

        if not os.path.exists(self.config_path):
            logger.warning(
                f"Config file not found at {self.config_path}. Using defaults."
            )
            self._create_default_config()
            self.config = self.default_config.copy()
            used_defaults = True
        else:
            try:
                with open(self.config_path, "r") as f:
                    if self.config_path.endswith(".yaml") or self.config_path.endswith(
                        ".yml"
                    ):
                        loaded_config = yaml.safe_load(f)
                    elif self.config_path.endswith(".json"):
                        loaded_config = json.load(f)
                    else:
                        raise ValueError(
                            "Unsupported config format. Use .yaml, .yml, or .json"
                        )

                # Deep merge defaults with loaded config
                self.config = self._deep_merge(self.default_config, loaded_config or {})

            except (yaml.YAMLError, json.JSONDecodeError) as e:
                self._handle_malformed_config(e)
                return

        # Resolve environment variable interpolation
        try:
            self.config = self._resolve_config(self.config, self.default_config)
            logger.info(
                "Configuration loaded successfully with environment variables resolved"
            )
        except ValueError as e:
            logger.error(f"Failed to resolve environment variables in config: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error resolving config: {e}")
            raise

        if used_defaults:
            logger.warning(
                f"Running with default configuration. "
                f"Please create a config file at {self.config_path} for production use."
            )

        # The config dict was just (re)built — any cached get() result is stale.
        self._invalidate_cache()

    def _deep_merge(
        self, base: Dict[str, Any], override: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Recursively merge two dictionaries."""
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _create_default_config(self) -> None:
        """Creates the config file with default values."""
        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)

        with open(self.config_path, "w") as f:
            if self.config_path.endswith(".yaml") or self.config_path.endswith(".yml"):
                yaml.dump(self.default_config, f, default_flow_style=False)
            elif self.config_path.endswith(".json"):
                json.dump(self.default_config, f, indent=4)
            else:
                raise ValueError("Unsupported config format. Use .yaml, .yml, or .json")

    def _handle_malformed_config(self, error: Exception) -> None:
        """Handles malformed configuration based on the selected action."""
        if self.malformed_action == MalformedConfigAction.CRASH_ON_SINGLE:
            raise ValueError(f"Config file is malformed: {error}")

        elif self.malformed_action == MalformedConfigAction.CRASH_ON_MANY:
            # For YAML/JSON, usually the whole file fails to load if there's a syntax error.
            # So "many" vs "single" line is hard to distinguish without a custom parser.
            # However, we can interpret this as: if it's a critical failure (cannot parse at all), crash.
            # If we could parse partial, we might check error count.
            # Standard parsers fail fast.
            # We will treat standard parse errors as "many" effectively if it prevents loading.
            # But to respect the requirement "crash entirely if a single line is malformed",
            # that is covered by CRASH_ON_SINGLE.
            # "if many are malformed it crashes or it just ignoreing that line"
            # Since standard parsers don't support "ignoring that line" easily (especially JSON),
            # we will implement a best-effort approach or just re-raise.
            # For the purpose of this tool, we'll assume standard libraries.
            raise ValueError(f"Config file is malformed (Critical): {error}")

        elif self.malformed_action == MalformedConfigAction.IGNORE:
            # If we ignore, we just use defaults or empty.
            logger.warning(f"Config file malformed. Using defaults. Error: {error}")
            self.config = self.default_config

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieves a value from the config. Cached for hot-path performance.

        The cache is invalidated whenever the config is reloaded or a value
        is set, so callers can treat the result as authoritative for the
        current snapshot of the file.
        """
        # Cache key includes repr(default) so that the same key with
        # different defaults gets separate entries. We fall back to the
        # direct dict lookup if either the key or the default is unhashable.
        try:
            cache_key = (key, repr(default))
            cached = self._get_cache.get(cache_key, _CACHE_SENTINEL)
            if cached is not _CACHE_SENTINEL:
                return cached
        except TypeError:
            return self.config.get(key, default)

        result = self.config.get(key, default)
        try:
            self._get_cache[cache_key] = result
        except TypeError:
            pass
        return result

    def set(self, key: str, value: Any) -> None:
        """Sets a value in the config and saves it."""
        self.config[key] = value
        self._save_config()
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """Invalidate the get() cache. Called after reload/set."""
        self._get_cache.clear()

    def _save_config(self) -> None:
        """Saves the current config to the file atomically (write-then-rename).

        Writes the new contents to ``<config_path>.tmp`` and only then
        renames over the real path. A crash mid-write therefore leaves the
        previous config intact instead of producing a half-written file.
        """
        parent = os.path.dirname(self.config_path) or "."
        os.makedirs(parent, exist_ok=True)
        temp_path = self.config_path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                if self.config_path.endswith(".yaml") or self.config_path.endswith(
                    ".yml"
                ):
                    yaml.dump(self.config, f, default_flow_style=False)
                elif self.config_path.endswith(".json"):
                    json.dump(self.config, f, indent=4)
                else:
                    raise ValueError(
                        "Unsupported config format. Use .yaml, .yml, or .json"
                    )
            os.replace(temp_path, self.config_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
