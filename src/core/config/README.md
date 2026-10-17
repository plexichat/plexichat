# Configuration system

The Plexichat configuration system loads settings from a YAML or JSON file,
deep-merges them with programmatic defaults, and applies environment-variable
overrides.

## Architecture

The configuration pipeline has three layers:

1. **Programmatic defaults** (`config_defaults.py`) -- the baseline used when
   no config file exists.
2. **User config file** (`config.yaml` or `config.json`) -- merged on top of
   defaults with a deep merge.
3. **Environment overrides** (`config_loader.py`) -- applied last so
   environment variables always win.

## Config search order

When the server starts, `setup_config` searches for an existing config file in
the following order:

1. `~/.plexichat/config/config.yaml` (home directory)
2. `<project_root>/config/config.yaml` (project directory)

If neither file exists, the home-directory path is created as the default.

## Environment variable interpolation

Any string value in a config file can reference an environment variable:

```
${VAR}          # Required -- raises ValueError if unset
${VAR:-default} # Optional -- falls back to `default` if unset
```

Interpolation is resolved recursively across dicts, lists, and scalar values.

## Type coercion

When a resolved string value has a typed counterpart in the default config
(an `int`, `float`, or `bool` default), the loader automatically coerces the
resolved string to the matching type.  Bare strings without a typed default
are left unchanged.

## Key modules

| Module | Purpose |
|---|---|
| `config_defaults.py` | Top-level defaults returned by `get_default_config()`. |
| `config_defaults_authentication.py` | Authentication-specific defaults. |
| `config_defaults_artifacts.py` | Artifacts-specific defaults. |
| `server/config_loader.py` | Loads the config file, applies env overrides, and exposes `setup_config()`. |
| `utils/config/` | Generic `ConfigLoader` utility shared across the codebase. |

## Usage

```python
# In your main entry point (once):
from config_defaults import get_default_config
from server.config_loader import setup_config

config_path = setup_config(
    project_root="/path/to/project",
    default_config=get_default_config(),
)

# In any module (no setup needed):
import utils.config as config

value = config.get("database.type")
```

## Related documentation

- [Config utility README](../../../utils/common_utils/utils/config/README.md)
