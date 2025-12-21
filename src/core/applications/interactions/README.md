# Interactions

User interaction handling for buttons, select menus, and modals.

## Components

- `components.py` - UI component builders (buttons, selects, modals)
- `responses.py` - Interaction response creators
- `handler.py` - InteractionHandler for processing interactions

## Usage

```python
from src.core.applications.interactions import (
    build_button,
    build_action_row,
    create_message_response,
    InteractionHandler,
)

button = build_button(label="Click me", custom_id="btn_1")
row = build_action_row([button])
```

## Response Types

- Message response
- Deferred response
- Modal response
- Autocomplete response
- Update response
