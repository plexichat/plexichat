# AutoMod Manager

## Purpose
Executes automatic moderation rules, evaluates message content, and
applies configured actions with audit tracking.

## Layout
- `__init__.py` — Thin re-export of `AutoModManager` from `base.py`
- `base.py` — `AutoModManager` class composed via mixins; `__init__`, `_load_config`, `_init_ai_adapters`, `reload_config`
- `rules.py` — `RuleOpsMixin`: rule CRUD, enable/disable, default rules
- `evaluation.py` — `EvaluationMixin`: message/user/bulk/ai checking, rule evaluation
- `actions.py` — `ActionMixin`: violation processing, action execution, manual triggers
- `exemptions.py` — `ExemptionMixin`: exemption checks and management
- `reputation.py` — `ReputationMixin`: user reputation scoring and decay
- `audit.py` — `AuditMixin`: audit log retrieval
- `tracking.py` — `TrackingMixin`: rate-limit window tracking
- `converters.py` — Standalone row-to-model converter functions

## Primary Responsibilities
- Load and evaluate automod rules per server
- Apply rule actions such as delete, timeout, kick, or ban
- Integrate AI moderation adapters when configured
- Track violations and user reputation signals
- Provide exemptions and rule-scoped bypasses

## Supported Rule Types
- `KEYWORD` — Block or flag messages containing specific keywords.
- `REGEX` — Pattern-based content filtering.
- `MESSAGE_SPAM` — Rate-limit detection for rapid messaging.
- `MENTION_SPAM` — Rate-limit detection for excessive @mentions.
- `INVITE_LINKS` — Block or flag server invite links.
- `EXTERNAL_LINKS` — Block or flag external URLs.
- `CAPS_PERCENTAGE` — Flag messages with excessive capitalization.
- `MASS_EMOJI` — Flag messages with excessive emoji usage.
- `REPEATED_CHARS` — Flag messages with repeated characters.
- `AI_MODERATION` — Third-party AI moderation (OpenAI, Perspective, custom).

## Supported Action Types
- `DELETE_MESSAGE` — Remove the offending message.
- `TIMEOUT_USER` — Temporarily mute the user in the server.
- `KICK_USER` — Remove the user from the server.
- `BAN_USER` — Permanently ban the user from the server.
- `ALERT_MODERATORS` — Notify moderators via the notification system.

## Usage

```python
from src.core.automod.manager import AutoModManager

am = AutoModManager(db, servers_module=servers, messaging_module=messaging,
                    notifications_module=notifications)

# Ensure default rules exist for a server
am.ensure_default_rules(server_id=1, owner_id=1)

# Create a keyword rule
rule = am.create_rule(
    server_id=1,
    created_by=1,
    rule_type="keyword",
    name="No Swearing",
    trigger_config={"keywords": ["badword1", "badword2"]},
    action_config={"action": "delete_message"},
    enabled=True
)

# Check a message against all enabled rules
result = am.check_message(message_id=42, server_id=1, channel_id=10, user_id=2, content="This is a test message")
# Returns CheckResult with triggered_rules, action_taken, violation_id

# Manually trigger a violation
violation = am.trigger_violation(
    server_id=1,
    user_id=2,
    rule_id=rule.id,
    reason="Manual review",
    action_by=1
)
```

## Error Handling

- `ValueError` — Invalid rule type, action type, or trigger configuration.
- `PermissionError` — User lacks permission to create/manage rules for the server.
- Rule evaluation failures are caught per-rule: if one rule's evaluation raises an exception, it is logged and other rules continue processing.

```python
try:
    rule = am.create_rule(
        server_id=1, created_by=1, rule_type="invalid_type",
        name="Test", trigger_config={}, action_config={}, enabled=True
    )
except ValueError as e:
    print(f"Invalid rule configuration: {e}")
```

## Dependencies
- Servers module for moderation actions (kick, ban, timeout).
- Messaging module for message deletion and content retrieval.
- Notifications module for moderator alerts (`ALERT_MODERATORS` action).

## Configuration
- `enabled`: True (default) — Master toggle for automod.
- `default_actions`: `["delete_message", "alert_moderators"]`.
- `rate_limit_window`: 60 seconds.
- `reputation_decay_rate`: 1.0, `reputation_decay_interval`: 86400 seconds (24h).
- `max_violations_before_action`: 1.
- AI adapters configured under `automod.ai` in config:
  - `openai.api_key` — OpenAI moderation endpoint.
  - `perspective.api_key` — Google Perspective API.
  - `custom.endpoint_url` — Custom AI moderation endpoint.
