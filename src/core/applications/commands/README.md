# Application commands

Register, validate, and manage slash commands and context-menu commands.

## Overview

The commands package handles command registration for the applications platform. You use it through `CommandRegistry` in `registry.py:23` or through the top-level `applications` API. The package validates names, descriptions, options, and permission strings before writing to `app_commands`, and it enforces duplicate-name and quota limits.

## Components

| File | Purpose |
|---|---|
| `registry.py` | `CommandRegistry` manages registration, lookup, update, deletion, and bulk overwrite. |
| `options.py` | Helpers for building and validating command options and choices. |
| `validation.py` | Validation for command names, descriptions, and overall command payloads. |

## Before you begin

Verify the following:

* The applications module is initialized with `applications.setup(db, auth, servers, events)`.
* The `max_commands_per_app` configuration is set (default `100` in `registry.py:93`).

## Validation rules

Validation is defined in `validation.py:18`.

### Names

* **Length** – Maximum `32` characters. See `MAX_COMMAND_NAME_LENGTH` in `validation.py:13`.
* **Chat-input commands** (`CHAT_INPUT`) – Must match `^[\w-]{1,32}$` and must be lowercase. See `COMMAND_NAME_PATTERN` in `validation.py:11` and the check in `validation.py:46`. The registry lowercases names on write in `registry.py:121`.
* **Context-menu commands** (`USER`, `MESSAGE`) – Must match `^[\w\s-]{1,32}$` and may include spaces. See `CONTEXT_MENU_NAME_PATTERN` in `validation.py:12`. The registry preserves original casing for these types in `registry.py:119`.

### Descriptions

* **Chat-input commands** – Required and maximum `100` characters. See `validation.py:72`.
* **Context-menu commands** – Optional and maximum `100` characters.

### Options

* Only chat-input commands can have options. See `validation.py:118`.
* Maximum `25` options per command. See `MAX_OPTIONS_PER_COMMAND` in `validation.py:15` and the check in `registry.py:93` context.
* Each option is validated through `validate_options` in `options.py`, which checks type, required fields, choice limits, channel types, and min or max constraints.

### Other fields

* `default_member_permissions` must be a valid permission integer string when provided. See `validation.py:130`.

## Use the package

### Register a command

```python
from src.core.applications import CommandType, CommandOptionType
from src.core.applications.commands import CommandRegistry

registry = CommandRegistry(db, config)

cmd = registry.register_command(
    application_id=app_id,
    name="ping",
    description="Check bot latency",
    command_type=CommandType.CHAT_INPUT,
    options=[
        {
            "name": "message",
            "description": "Message to echo",
            "type": CommandOptionType.STRING,
            "required": True,
        }
    ],
)

# Guild-specific command
guild_cmd = registry.register_command(
    application_id=app_id,
    name="serverinfo",
    description="Get server information",
    server_id=server_id,
)
```

### Handle duplicates and quotas

The `app_commands` table enforces `UNIQUE(application_id, name, server_id)` in `schema.py:48`. Registration closes the time-of-check to time-of-use (TOCTOU) race by relying on the database constraint and converting a uniqueness violation into `CommandValidationError` in `registry.py:158`. The registry also checks the count against `max_commands_per_app` per scope (global when `server_id IS NULL`, guild when `server_id` is set) in `registry.py:94`.

### Look up and update commands

```python
# Retrieve a single command
cmd = registry.get_command(command_id)

# List commands for an application
global_cmds = registry.get_commands(app_id)
guild_cmds = registry.get_commands(app_id, server_id=server_id, include_global=True)

# Update a command
updated = registry.update_command(
    command_id=cmd.id,
    description="Updated description",
    options=[{"name": "target", "description": "Target user", "type": CommandOptionType.USER}],
)

# Delete a command
registry.delete_command(command_id)
```

Updates validate the payload through `validate_command_update` in `validation.py:140` and check for duplicate names with the same case-sensitivity rules as registration in `registry.py:303`.

### Bulk overwrite commands

```python
# Replace all commands for an application atomically
commands = registry.bulk_overwrite_commands(
    application_id=app_id,
    server_id=None,  # None for global, set server_id for guild commands
    commands=[
        {"name": "ping", "description": "Check latency", "options": []},
        {"name": "echo", "description": "Echo a message", "options": [...]},
    ],
)
```

`bulk_overwrite_commands` in `registry.py:397` validates every command before any mutation and then deletes existing commands and inserts the new set within a single transaction (`begin_transaction`/`commit`/`rollback`). If validation fails, no commands are deleted.

## Build options

```python
from src.core.applications.commands import build_option, validate_options

option = build_option(
    name="count",
    description="Number of items",
    option_type=CommandOptionType.INTEGER,
    required=True,
    min_value=1,
    max_value=100,
)

valid, issues = validate_options([option])
```

See `options.py` for optional fields such as `choices`, `channel_types`, `min_length`, `max_length`, and `autocomplete`.

## Error handling

| Exception | When it is raised |
|---|---|
| `ApplicationNotFoundError` | The `application_id` does not exist in `app_applications`. |
| `CommandValidationError` | Name, description, options, or permissions are invalid, or a duplicate name exists. The exception includes an `issues` list. |
| `CommandNotFoundError` | The `command_id` does not exist. |
| `CommandLimitError` | The application has reached `max_commands_per_app`. |
