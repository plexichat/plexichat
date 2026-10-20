# Configuration manager

The configuration manager is the internal orchestrator that wires the three
configuration layers together and exposes the runtime config to the rest of
the application.

## Responsibilities

- **Load** -- reads the YAML/JSON config file from disk via
  `utils.config.ConfigLoader`.
- **Merge** -- deep-merges user overrides on top of the programmatic defaults
  from `config_defaults.get_default_config()`.
- **Override** -- applies environment-variable overrides
  (`config_loader._apply_env_overrides`) so that secrets and deployment
  settings are never committed to source control.
- **Expose** -- provides `get()`, `set()`, and `get_all()` helpers that the
  rest of the codebase calls to read configuration at runtime.

## Lifecycle

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   defaults   │ ───► │  config file │ ───► │  env overrides│
└──────────────┘      └──────────────┘      └──────────────┘
```

1. `get_default_config()` returns the full default dict.
2. `config.setup()` loads the user config file and deep-merges it.
3. `_apply_env_overrides()` patches database, monitoring, Valkey, and search
   settings from environment variables.
4. `config.set()` is called for each changed section, persisting the merged
   result back to disk atomically.

## Key classes

| Class | Location | Purpose |
|---|---|---|
| `ConfigLoader` | `utils/config/core.py` | Generic loader with YAML/JSON support, interpolation, and atomic saves. |
| `MalformedConfigAction` | `utils/config/core.py` | Enum controlling behaviour when the config file is malformed. |

## Atomic writes

`ConfigLoader._save_config()` writes to a temporary file in the same
directory and then renames over the original.  This prevents partial writes
if the process crashes mid-save and avoids file-lock contention between
concurrent `set()` calls.

## Caching

`ConfigLoader.get()` uses a dictionary cache keyed on `(key, repr(default))`.
The cache is invalidated on every `set()` or `reload()` call, so callers
always see a consistent snapshot.

## Related documentation

- [Configuration system README](../README.md)
- [Config utility README](../../../utils/common_utils/utils/config/README.md)
