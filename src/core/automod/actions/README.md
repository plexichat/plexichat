# Automod Actions

Moderation actions executed when rules are triggered.

## Actions

- `delete.py` - DeleteMessageAction - Remove offending message
- `timeout.py` - TimeoutUserAction - Temporarily mute user
- `kick.py` - KickUserAction - Remove user from server
- `ban.py` - BanUserAction - Permanently ban user
- `alert.py` - AlertModeratorsAction - Notify moderators

## Usage

```python
from src.core.automod.actions import DeleteMessageAction, TimeoutUserAction

action = DeleteMessageAction()
await action.execute(context)
```

## Base Class

All actions extend `BaseAction` which defines the `execute()` interface.
