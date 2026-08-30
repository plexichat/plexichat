# AutoMod manager

## Overview

The AutoMod manager evaluates messages against configured rules and applies moderation actions. It loads rules per server, integrates optional AI adapters, and tracks violations, reputation, and audit events.

This module is server-side. It does not expose the WebSocket gateway or health endpoints directly, but it operates alongside them.

*   WebSocket gateway: ` /gateway` — see `plexichat/src/api/websocket/gateway.py:261`. Use `/gateway`, not `/ws`.
*   Readiness probe: `GET /api/v1/ready` — see `plexichat/src/api/routes/health.py:18`.
*   Deep health check: `GET /api/v1/health` — see `plexichat/src/api/routes/health.py:46` with a root alias at `GET /health` — see `plexichat/src/api/app.py:574`.

## Prerequisites

*   A configured `Database` instance.
*   Optional: `servers` module for kick, ban, and timeout actions.
*   Optional: `messaging` module for message deletion and retrieval.
*   Optional: `notifications` module for the `ALERT_MODERATORS` action.

## Layout

| File | Purpose |
| --- | --- |
| `__init__.py` | Re-exports `AutoModManager` from `base.py`. |
| `base.py` | Defines `AutoModManager` composition, `__init__`, `_load_config`, `_init_ai_adapters`, and `reload_config`. |
| `rules.py` | `RuleOpsMixin` — rule CRUD, enable, disable, and default rules. |
| `evaluation.py` | `EvaluationMixin` — message, user, bulk, and AI evaluation. |
| `actions.py` | `ActionMixin` — violation processing and manual triggers. |
| `exemptions.py` | `ExemptionMixin` — exemption checks and management. |
| `reputation.py` | `ReputationMixin` — reputation scoring and decay. |
| `audit.py` | `AuditMixin` — audit log retrieval. |
| `tracking.py` | `TrackingMixin` — rate-limit window tracking. |
| `converters.py` | Standalone row-to-model converters. |

## Responsibilities

*   Load and evaluate AutoMod rules per server.
*   Apply actions such as delete, timeout, kick, or ban.
*   Integrate AI moderation adapters when configured.
*   Track violations and reputation signals.
*   Provide exemptions and rule-scoped bypasses.

## Rule types

The manager maps `RuleType` values to rule classes — see `plexichat/src/core/automod/manager/base.py:51`.

| Type | Description |
| --- | --- |
| `KEYWORD` | Block or flag messages that contain keywords. |
| `REGEX` | Filter content with regular expressions. |
| `MESSAGE_SPAM` | Detect rapid messaging. |
| `MENTION_SPAM` | Detect excessive `@mentions`. |
| `INVITE_LINKS` | Block or flag server invite links. |
| `EXTERNAL_LINKS` | Block or flag external URLs. |
| `CAPS_PERCENTAGE` | Flag messages with excessive capitalization. |
| `MASS_EMOJI` | Flag messages with excessive emoji. |
| `REPEATED_CHARS` | Flag messages with repeated characters. |
| `AI_MODERATION` | Evaluate content with AI adapters. |

## Action types

The manager maps `ActionType` values to action classes — see `plexichat/src/core/automod/manager/base.py:64`.

| Type | Description |
| --- | --- |
| `DELETE_MESSAGE` | Delete the offending message. |
| `TIMEOUT_USER` | Temporarily mute the user in the server. |
| `KICK_USER` | Remove the user from the server. |
| `BAN_USER` | Ban the user from the server. |
| `ALERT_MODERATORS` | Notify moderators through the notifications system. |

## Usage

### Initialize the manager

```python
from src.core.automod.manager import AutoModManager

am = AutoModManager(
    db,
    servers_module=servers,
    messaging_module=messaging,
    notifications_module=notifications,
)

# Ensure default rules exist for a server
am.ensure_default_rules(server_id=1, owner_id=1)
```

### Create a rule

```python
rule = am.create_rule(
    server_id=1,
    created_by=1,
    rule_type="keyword",
    name="No Swearing",
    trigger_config={"keywords": ["badword1", "badword2"]},
    action_config={"action": "delete_message"},
    enabled=True,
)
```

### Check a message

```python
result = am.check_message(
    message_id=42,
    server_id=1,
    channel_id=10,
    user_id=2,
    content="This is a test message",
)
# result.triggered_rules, result.action_taken, result.violation_id
```

### Trigger a violation manually

```python
violation = am.trigger_violation(
    server_id=1,
    user_id=2,
    rule_id=rule.id,
    reason="Manual review",
    action_by=1,
)
```

## Configuration

The manager reads `automod` from the app config — see `plexichat/src/config_defaults.py:668` and `plexichat/src/core/automod/manager/base.py:86`.

```yaml
automod:
  enabled: true
  exempt_owners: true
  exempt_admins: true
  fail_closed: true
  default_alert_channel_id: null
  rules:
    caps:
      enabled: true
      max_percentage: 70.0
      min_length: 10
      ignore_commands: true
```

Manager defaults merged in `base.py:87` are:

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Master toggle for AutoMod. |
| `default_actions` | `["delete_message", "alert_moderators"]` | Fallback actions when a rule has no explicit action. |
| `rate_limit_window` | `60` | Rate-limit window in seconds. |
| `reputation_decay_rate` | `1.0` | Reputation decay rate. |
| `reputation_decay_interval` | `86400` | Decay interval in seconds (24 hours). |
| `max_violations_before_action` | `1` | Violations before an action fires. |

AI adapters are configured under `automod.ai` — see `plexichat/src/core/automod/manager/base.py:99`:

```yaml
automod:
  ai:
    shieldstral:
      endpoint_url: ""
      api_key: ""
      model: "shieldstral"
      threshold: 0.5
      timeout_seconds: 60
```

The manager also supports `openai`, `perspective`, and `custom` adapters when their `api_key` or `endpoint_url` is set.

Global server config keys use these paths — see `plexichat/src/config_defaults.py`:

*   `server.host`, `server.port`, `server.reload` — see `plexichat/src/config_defaults.py:54`. Do not use `api.host` or `api.port`.
*   `api.api_prefix` — default `/api/v1` — see `plexichat/src/config_defaults.py:131`. Do not use `api.prefix` or `prefix`.
*   `tls` — see `plexichat/src/config_defaults.py:599`. Do not use `api.ssl`.
*   `voice.sfu_backend` — default `aiortc` — see `plexichat/src/config_defaults.py:399`. Do not use `mediasoup`.

## Gateway opcodes and intents

If you integrate AutoMod events with the gateway, use these values — see `plexichat/src/api/websocket/opcodes.py:40` and `plexichat/src/core/events/types.py:120`:

*   Opcodes: `50 RATCHET_UPDATE`, `60 ARTIFACT_SUBSCRIBE`, `61 ARTIFACT_UNSUBSCRIBE`, `62 ARTIFACT_OP`, `63 ARTIFACT_SYNC`, `64 SUM_SUBSCRIBE`, `65 SUM_UNSUBSCRIBE`, `66 SUM_OP`, `67 SUM_SYNC`.
*   Intents: `1<<20 ARTIFACTS` and `1<<21 SUMS` (alongside `1<<1 GUILD_MEMBERS` and `1<<8 GUILD_PRESENCES` when needed).

## Error handling

| Error | Cause |
| --- | --- |
| `ValueError` | Invalid rule type, action type, or trigger configuration. |
| `PermissionError` | User lacks permission to manage rules for the server. |

Rule evaluation failures are isolated per rule. If one rule raises, the manager logs the error and continues to the next rule.

```python
try:
    rule = am.create_rule(
        server_id=1,
        created_by=1,
        rule_type="invalid_type",
        name="Test",
        trigger_config={},
        action_config={},
        enabled=True,
    )
except ValueError as e:
    print(f"Invalid rule configuration: {e}")
```

## Dependencies

*   `servers` module for moderation actions.
*   `messaging` module for message content and deletion.
*   `notifications` module for `ALERT_MODERATORS`.
