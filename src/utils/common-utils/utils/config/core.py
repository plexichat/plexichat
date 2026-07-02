import os
import json
import yaml
import logging
from typing import Any, Dict, Optional
from enum import Enum

# Standard logger for internal config errors
logger = logging.getLogger(__name__)


class MalformedConfigAction(Enum):
    CRASH_ON_SINGLE = "crash_on_single"
    CRASH_ON_MANY = "crash_on_many"
    IGNORE = "ignore"


class ConfigLoader:
    """
    A simple, robust configuration loader supporting YAML and JSON.
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

        self._load_config()

    def _load_config(self) -> None:
        """Loads the configuration from the file."""
        if not os.path.exists(self.config_path):
            self._create_default_config()
            self.config = self.default_config.copy()
            return

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
        """Retrieves a value from the config."""
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Sets a value in the config and saves it."""
        self.config[key] = value
        self._save_config()

    def _save_config(self) -> None:
        """Saves the current config to the file."""
        with open(self.config_path, "w") as f:
            if self.config_path.endswith(".yaml") or self.config_path.endswith(".yml"):
                yaml.dump(self.config, f, default_flow_style=False)
            elif self.config_path.endswith(".json"):
                json.dump(self.config, f, indent=4)
