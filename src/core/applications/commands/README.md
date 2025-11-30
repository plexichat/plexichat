# Application Commands

Slash command registration and validation.

## Components

- `registry.py` - CommandRegistry for managing registered commands
- `options.py` - Command option building and validation
- `validation.py` - Command name and description validation

## Usage

```python
from src.core.applications.commands import (
    CommandRegistry,
    build_option,
    validate_command,
)

registry = CommandRegistry()
registry.register(command)
```

## Features

- Command name/description validation
- Option type validation
- Command registration and lookup
