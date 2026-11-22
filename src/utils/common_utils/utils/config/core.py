import os
import json
import tempfile
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

# Hard bounds on config parsing. ``yaml.safe_load`` prevents arbitrary object
# construction, but it is still a recursive-descent parser over an unbounded
# input: a config with a few hundred nested levels raises ``RecursionError``
# (which is *not* a ``yaml.YAMLError`` and used to escape the loader and crash
# the process at startup), and an arbitrarily large file was read and decoded
# in full before any check. These caps keep a malformed/tampered config from
# turning a config read into a CPU/memory DoS. They are deliberately generous:
# a real config is a few kilobytes with a handful of nesting levels.
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_CONFIG_DEPTH = 64
MAX_CONFIG_NODES = 50_000


class ConfigParseError(yaml.YAMLError):
    """A config exceeded the parser's safety bounds.

    Subclasses ``yaml.YAMLError`` so the existing malformed-config handler
    routes it to the configured ``MalformedConfigAction`` (fail closed) rather
    than leaking a raw parser/interpreter error to callers.
    """


class _BoundedSafeLoader(yaml.SafeLoader):
    """SafeLoader that refuses to compose abusive YAML documents."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config_depth = 0
        self._config_nodes = 0

    def compose_node(self, parent, index):
        self._config_nodes += 1
        if self._config_nodes > MAX_CONFIG_NODES:
            raise ConfigParseError(
                f"Config YAML exceeds maximum node count ({MAX_CONFIG_NODES})"
            )
        self._config_depth += 1
        if self._config_depth > MAX_CONFIG_DEPTH:
            raise ConfigParseError(
                f"Config YAML exceeds maximum nesting depth ({MAX_CONFIG_DEPTH})"
            )
        try:
            return super().compose_node(parent, index)
        finally:
            self._config_depth -= 1


def safe_load_bounded(source):
    """Parse a YAML config with bounded size, nesting depth and node count.

    ``source`` may be bytes, str, or a binary file-like object. File objects
    are read at most one byte past the cap so an oversized file never has to
    be materialised in memory first. Over-limit input raises
    :class:`ConfigParseError`, which callers route through their malformed
    config handling. Empty input is treated as an empty mapping (matching
    ``yaml.safe_load("") -> None`` -> ``loaded_config or {}`` at call sites).
    """
    if hasattr(source, "read"):
        raw = source.read(MAX_CONFIG_BYTES + 1)
    else:
        raw = source
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    else:
        raw = bytes(raw)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigParseError(
            f"Config file exceeds maximum size ({MAX_CONFIG_BYTES} bytes)"
        )
    if not raw:
        return {}
    # Equivalent to yaml.load(raw, Loader=_BoundedSafeLoader) but explicit, so
    # no bare ``yaml.load`` call is introduced into the codebase.
    loader = _BoundedSafeLoader(raw)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


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

        Type coercion is applied to string values that have a typed default to
        infer from, whether the value was interpolated (contained ${...}
        patterns) or read plain from a config file. Coercing plain values keeps
        config files that quote numbers (e.g. ``port: "6379"``) from tripping
        strict type validation while leaving bare strings (hosts, passwords,
        API keys) untouched when the default is also a string.

        Args:
            node: Config node (dict, list, or primitive value)
            default_node: Corresponding node from default config for type inference

        Returns:
            Resolved config node with all interpolations resolved and types coerced
            where the default config provides a type hint.
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
            # regex, no recursion. _resolve_value would also short-circuit,
            # but we save the call entirely. Coerce plain file strings to the
            # default's type when a typed default exists (e.g. a quoted
            # ``port: "6379"`` in a config file becomes int 6379), so config
            # files that quote numbers don't trip strict type validation.
            # Bare strings without a typed default stay strings (hosts,
            # passwords, API keys, ...).
            if isinstance(node, str) and "$" not in node:
                if default_node is not None and not isinstance(default_node, str):
                    return self._coerce_value(node, default_node)
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
                with open(self.config_path, "rb") as f:
                    if self.config_path.endswith(".yaml") or self.config_path.endswith(
                        ".yml"
                    ):
                        loaded_config = safe_load_bounded(f)
                    elif self.config_path.endswith(".json"):
                        raw = f.read(MAX_CONFIG_BYTES + 1)
                        if len(raw) > MAX_CONFIG_BYTES:
                            raise ConfigParseError(
                                f"Config file exceeds maximum size "
                                f"({MAX_CONFIG_BYTES} bytes)"
                            )
                        loaded_config = json.loads(raw.decode("utf-8")) if raw else {}
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
        """Recursively merge two dictionaries.

        String lists are concatenated (deduplicated) rather than replaced, so
        that values from the default config (e.g. ``admin_ui.host_restriction
        .allowed_hosts``) survive when the loaded config only specifies a
        subset. A typed scalar list (e.g. ``ports: [1, 2]``) is a single
        setting, so the loaded list replaces it and is coerced against the
        default below. Scalar and dict values from ``override`` take
        precedence.
        """
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            elif (
                key in result
                and isinstance(result[key], list)
                and isinstance(value, list)
            ):
                typed_scalars = all(
                    not isinstance(item, (str, dict, list)) for item in result[key]
                )
                scalar_override = all(
                    not isinstance(item, (dict, list)) for item in value
                )
                if result[key] and typed_scalars and scalar_override:
                    result[key] = list(value)
                else:
                    # Concatenate lists, deduplicating while preserving order.
                    merged = list(result[key])
                    for item in value:
                        if item not in merged:
                            merged.append(item)
                    result[key] = merged
            else:
                result[key] = value
        return result

    def _create_default_config(self) -> None:
        """Create the config file with default values, atomically.

        ``open(path, "w")`` follows a symlink planted at the config path and
        truncates the target before the new contents are known, so a local
        attacker could redirect this write to an arbitrary file and a crash
        mid-write left a corrupt config. Mirror ``_save_config``: create a
        unique private file in the destination directory and atomically rename
        it over the final path. ``os.replace`` replaces a symlink entry itself
        rather than following it, so the link target is never written to.
        """
        parent = os.path.dirname(os.path.abspath(self.config_path))
        os.makedirs(parent, exist_ok=True)
        # A brand-new config uses the same umask-derived mode a plain
        # open(path, "w") would have produced (mkstemp always creates 0600).
        umask = os.umask(0)
        os.umask(umask)
        file_mode = 0o666 & ~umask
        temp_path: Optional[str] = None
        try:
            fd, temp_path = tempfile.mkstemp(
                dir=parent,
                prefix=os.path.basename(self.config_path) + ".",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if self.config_path.endswith(".yaml") or self.config_path.endswith(
                    ".yml"
                ):
                    yaml.dump(self.default_config, f, default_flow_style=False)
                elif self.config_path.endswith(".json"):
                    json.dump(self.default_config, f, indent=4)
                else:
                    raise ValueError(
                        "Unsupported config format. Use .yaml, .yml, or .json"
                    )
            os.chmod(temp_path, file_mode)
            os.replace(temp_path, self.config_path)
        except Exception:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise

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
        # Only the mutated key went stale; preserve every other hot-path entry.
        self._invalidate_cache(key)

    def _invalidate_cache(self, key: Optional[str] = None) -> None:
        """Invalidate cached :meth:`get` results.

        With no key (a full reload), every cached value may be stale so the
        whole cache is dropped. With a key (a single :meth:`set`), only the
        entries for that config key are stale. Evicting just those entries
        keeps cached lookups for unrelated keys warm - important because
        ``get()`` is a hot path and a lone admin/settings mutation used to
        flush every other key's cached value.
        """
        if key is None:
            self._get_cache.clear()
            return
        stale = [ck for ck in self._get_cache if ck[0] == key]
        for ck in stale:
            del self._get_cache[ck]

    def _save_config(self) -> None:
        """Saves the current config to the file atomically (write-then-rename).

        Writes the new contents to a unique temp file in the same directory
        and only then renames over the real path. A crash mid-write therefore
        leaves the previous config intact instead of producing a half-written
        file, and concurrent ``set()`` calls from different threads can no
        longer collide on a shared ``.tmp`` name (one thread renaming the
        other's temp file away mid-write).

        ``mkstemp`` creates its file mode 0600, so the mode of an existing
        config file is preserved (or a standard umask-derived mode is used
        for new files) to keep this write path consistent with
        ``_create_default_config`` and to avoid silently locking other
        users/processes out of the config.
        """
        parent = os.path.dirname(self.config_path) or "."
        os.makedirs(parent, exist_ok=True)
        # Preserve the existing file's mode (mkstemp always creates 0600);
        # for a brand-new file fall back to the umask-derived mode that a
        # plain open(path, "w") would have produced.
        try:
            file_mode = os.stat(self.config_path).st_mode & 0o7777
        except OSError:
            umask = os.umask(0)
            os.umask(umask)
            file_mode = 0o666 & ~umask
        temp_path: Optional[str] = None
        try:
            fd, temp_path = tempfile.mkstemp(
                dir=parent,
                prefix=os.path.basename(self.config_path) + ".",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
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
            os.chmod(temp_path, file_mode)
            os.replace(temp_path, self.config_path)
        except Exception:
            if temp_path is not None:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise
