# Automod actions

This directory contains the moderation actions that run when an AutoMod rule triggers. Each action extends `BaseAction` and executes through the AutoMod manager.

## File layout

| File | Class | Responsibility |
| --- | --- | --- |
| `base.py` | `BaseAction` | Abstract base class that defines `execute()` and `can_execute()` and holds `self._db`, `self._servers`, `self._messaging`, `self._notifications` |
| `delete.py` | `DeleteMessageAction` | Deletes the offending message or, for async AI checks, replaces it with a flag-driven tombstone |
| `timeout.py` | `TimeoutUserAction` | Temporarily mutes the user in the server (`duration_seconds`, default 300s) |
| `kick.py` | `KickUserAction` | Removes the user from the server |
| `ban.py` | `BanUserAction` | Permanently bans the user and optionally deletes recent messages |
| `alert.py` | `AlertModeratorsAction` | Sends a notification to moderators via the notifications module |

All actions share the same constructor:

```python
BaseAction(db, servers_module=None, messaging_module=None, notifications_module=None)
```

## Base class

`BaseAction` (`src/core/automod/actions/base.py`) defines the interface you implement:

```python
from typing import Dict, Any, Optional
from src.core.automod.models import ActionType, RuleAction, Violation

class BaseAction(ABC):
    action_type: Optional[ActionType] = None

    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Execute the action. Return True on success."""
        ...

    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Check whether the action can run. Return (True, None) or (False, reason)."""
        ...
```

The manager calls `can_execute()` before `execute()`. Return `False` with a reason to skip the action without logging an error.

Common context keys:

| Key | Type | Description |
| --- | --- | --- |
| `bot_user_id` | `int` | Actor for permission-checked deletes |
| `__system_context__` | `bool` | Whether the caller is the system (allows owner moderation) |
| `__automod_replacement__` | `bool` | Whether to tombstone instead of hard-delete (AI path) |
| `__automod_reason__` | `str` | Reason to store in the tombstone metadata |

## Use an action

You do not call actions directly in normal flows. The manager resolves them:

```python
from src.core import automod

# The manager maps ActionType.DELETE_MESSAGE -> DeleteMessageAction, etc.
result = automod.check_message_fast(
    server_id=server.id, channel_id=channel.id, user_id=user.id, content=content
)
if not result.passed:
    for match in result.violations:
        automod.process_violation(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            message_id=message.id,
            match=match,
            actions=result.actions_to_take,
        )
```

To test an action in isolation:

```python
from src.core.automod.actions.delete import DeleteMessageAction
from src.core.automod.models import RuleAction, ActionType, Violation

action_cfg = RuleAction(action_type=ActionType.DELETE_MESSAGE)
violation = Violation(
    id=1, server_id=server.id, channel_id=channel.id,
    user_id=user.id, message_id=message.id,
    rule_id=rule.id, rule_type=rule.rule_type,
    matched_content="bad word", actions_taken=[], severity=rule.severity,
    created_at=123, metadata={}
)

deleter = DeleteMessageAction(db, servers_module=servers, messaging_module=messaging)
if deleter.can_execute(action_cfg, violation)[0]:
    ok = deleter.execute(action_cfg, violation, context={"bot_user_id": bot.id})
```

## Action details

### Delete message (`delete.py`)

- Requires `violation.message_id`. Returns `False` if missing.
- Requires `self._messaging` to be set.
- Two modes:
  - **Hard delete** (fast path): calls `self._messaging.delete_message(user_id, message_id, hard_delete=True)` and broadcasts `MESSAGE_DELETE`. If `bot_user_id` is not in `context`, it requires `__system_context__=True` and uses `user_id=0` (system actor).
  - **Replacement** (AI path, `context["__automod_replacement__"]=True`): calls `self._messaging.automod_remove_message()` to blank content, set `automod_removed` metadata with `violation_id`, and broadcasts `MESSAGE_UPDATE`. The client renders the tombstone from the flag, not from content.

### Timeout user (`timeout.py`)

- Uses `action.duration_seconds` or `300` seconds default.
- Calls `self._servers.update_member(server_id, member_user_id, timeout_until, timeout_reason)` with `user_id=0` (system).
- `can_execute()` checks that the target is a member of `violation.server_id` and, unless `__system_context__` is true, rejects timing out the server owner.

### Kick user (`kick.py`)

- Removes the user from the server via `self._servers`. `can_execute()` rejects kicking the owner unless system context is set.

### Ban user (`ban.py`)

- Bans the user via `self._servers` and optionally deletes recent messages when `action.metadata["delete_message_days"]` is set.
- Respects the same owner check as kick and timeout.

### Alert moderators (`alert.py`)

- Sends a notification through `self._notifications` to the server's `system_channel_id` or `automod.default_alert_channel_id`. No permission check; it always logs.

## Create a custom action

1. Create `my_action.py` in this directory.
2. Extend `BaseAction`, set `action_type`, and implement `execute` and `can_execute`.
3. Register the mapping in `src/core/automod/manager/__init__.py` so `process_violation` can resolve it.

```python
from src.core.automod.actions.base import BaseAction
from src.core.automod.models import ActionType

class MyAction(BaseAction):
    action_type = ActionType.LOG_ONLY  # or a new ActionType you add to models.py

    def execute(self, action, violation, context=None) -> bool:
        # Your logic here
        return True
```

## Handle errors

Actions return `bool` instead of raising. Check the return value and read logs:

```python
ok = action.execute(cfg, violation, context)
if not ok:
    print("Action failed — check logs for can_execute reason or module availability")
```

If `self._messaging`, `self._servers`, or `self._notifications` is `None`, the action logs a warning and returns `False`.

## Related docs

- `src/core/automod/README.md` — Rule types, pipeline, and configuration.
- `src/core/automod/ai/README.md` — AI backends that trigger these actions.
- `src/api/schemas/messages.py:94` — `MessageResponse.read_by` is `List[ReaderInfo]` (used in tombstone broadcasts).
- `src/api/schemas/servers.py:85` — `ChannelCreateRequest.channel_type` field name.
