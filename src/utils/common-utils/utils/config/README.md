# Config Utility

A simple, robust configuration loader for Python applications.

## Features

- **YAML and JSON Support**: Works with both `.yaml` (preferred) and `.json` files.
- **Automatic Creation**: Creates a default config file if one doesn't exist.
- **Robust Error Handling**: Configurable behavior for malformed files (Crash or Ignore).
- **Simple Interface**: Easy `get` and `set` methods.

## Installation

Ensure this directory is in your Python path.
Requires `PyYAML`.

```bash
pip install PyYAML
```

## Usage

### Setup (Once in main.py)

In your main application file, setup the config loader once:

```python
import utils.config as config

# Default values if config file doesn't exist
defaults = {
    "app_name": "MyApp",
    "version": 1.0,
    "debug": True,
    "db_host": "localhost",
    "db_port": 5432
}

# Setup - do this ONCE in your main file
config.setup(config_path="config.yaml", default_config=defaults)

# Access values immediately
print(config.get("app_name"))
```

### Usage (In any other file)

In any other file in your project, just import and use:

```python
import utils.config as config

def connect_to_database():
    host = config.get("db_host")
    port = config.get("db_port")
    # Use the config values...

def update_setting():
    config.set("last_updated", "2024-01-01")
```

**No need to pass config objects around or configure again!**

### Legacy Usage (Still Supported)

You can also use the ConfigLoader class directly if you need multiple configs:

```python
from utils.config import ConfigLoader, MalformedConfigAction

defaults = {
    "app_name": "MyApp",
    "version": 1.0,
    "debug": True
}

config = ConfigLoader(
    config_path="config.yaml",
    default_config=defaults,
    malformed_action=MalformedConfigAction.CRASH_ON_SINGLE
)

print(config.get("app_name"))
```

### Configuration Options

| Option             | Description                  | Default           |
| ------------------ | ---------------------------- | ----------------- |
| `config_path`      | Path to the config file      | (Required)        |
| `default_config`   | Dictionary of default values | `{}`              |
| `malformed_action` | Action on load error         | `CRASH_ON_SINGLE` |

### Malformed Config Actions

- `MalformedConfigAction.CRASH_ON_SINGLE`: Raises an error if the file cannot be parsed.
- `MalformedConfigAction.CRASH_ON_MANY`: Raises an error if parsing fails critically.
- `MalformedConfigAction.IGNORE`: Logs a warning and uses default values if the file is malformed.

## Notes

- The `set` method automatically saves the configuration back to the file.
