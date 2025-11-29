# AutoMod Module

Auto-moderation system for PlexiChat supporting configurable rules, multiple action types, AI moderation backends, and user reputation scoring.

## Features

- Configurable rule engine with multiple rule types
- Multiple actions per rule (delete, timeout, kick, ban, alert)
- AI moderation backend support (OpenAI, Perspective API, custom endpoints)
- Per-server rule configuration with enable/disable
- Exempt roles and channels
- Configurable thresholds and cooldowns
- Audit logging of all automod actions
- Real-time rule evaluation on message create/edit
- Bulk message scanning for raids
- User reputation scoring based on violations

## Setup

```python
from src.core.database import Database
from src.core import auth, messaging, servers, notifications
from src.core import automod

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
notifications.setup(db, messaging, servers)

automod.setup(db, servers, messaging, notifications)
```

## Usage

### Check Messages

```python
from src.core import automod

result = automod.check_message(
    server_id=server.id,
    channel_id=channel.id,
    user_id=user.id,
    content="Check this message content"
)

if not result.passed:
    for violation in result.violations:
        print(f"Rule {violation.rule_type.value} matched: {violation.matched_content}")
    
    if result.should_delete:
        print("Message should be deleted")
```

### Create Rules

```python
rule = automod.create_rule(
    user_id=admin.id,
    server_id=server.id,
    name="Block bad words",
    rule_type=automod.RuleType.KEYWORD,
    rule_config={
        "keywords": ["spam", "scam"],
        "case_sensitive": False,
        "whole_word": True
    },
    actions=[
        {"action_type": "delete_message"},
        {"action_type": "alert_moderators"}
    ],
    exempt_roles=[mod_role.id],
    priority=10
)
```

### Rule Types

| Type | Description | Config Options |
|------|-------------|----------------|
| `KEYWORD` | Block specific keywords | `keywords`, `case_sensitive`, `whole_word`, `severity_map` |
| `REGEX` | Match regex patterns | `patterns` (list with `pattern`, `severity`, `name`) |
| `MESSAGE_SPAM` | Rate limiting | `max_messages`, `window_seconds`, `duplicate_threshold` |
| `MENTION_SPAM` | Excessive mentions | `max_user_mentions`, `max_role_mentions`, `block_everyone` |
| `INVITE_LINKS` | Block server invites | `block_all`, `allowed_codes`, `code_length` |
| `EXTERNAL_LINKS` | Filter external URLs | `mode` (whitelist/blacklist), `whitelist`, `blacklist` |
| `CAPS_PERCENTAGE` | Excessive caps | `max_percentage`, `min_length` |
| `MASS_EMOJI` | Too many emoji | `max_emoji`, `max_percentage` |
| `REPEATED_CHARS` | Character spam | `max_repeats`, `min_occurrences` |

### Action Types

| Action | Description | Options |
|--------|-------------|---------|
| `DELETE_MESSAGE` | Delete the message | - |
| `TIMEOUT_USER` | Temporarily mute user | `duration_seconds` |
| `KICK_USER` | Remove from server | `reason` |
| `BAN_USER` | Permanently ban | `reason`, `delete_message_days` |
| `ALERT_MODERATORS` | Notify mods | - |
| `LOG_ONLY` | Just log, no action | - |

### Exemptions

```python
automod.add_exemption(
    user_id=admin.id,
    server_id=server.id,
    target_type="role",
    target_id=trusted_role.id,
    rule_id=None
)

automod.add_exemption(
    user_id=admin.id,
    server_id=server.id,
    target_type="channel",
    target_id=bot_channel.id,
    rule_id=rule.id
)
```

### User Reputation

```python
reputation = automod.get_user_reputation(user.id, server.id)
print(f"Score: {reputation.score}/100")
print(f"Violations: {reputation.violation_count}")

automod.decay_reputation(server.id)
```

### AI Moderation

```python
result = automod.check_ai(
    content="Check this content",
    backend="openai"
)

if result.flagged:
    print(f"Flagged categories: {result.categories}")
    print(f"Scores: {result.scores}")
```

### Audit Log

```python
entries = automod.get_audit_log(server.id, limit=50)

for entry in entries:
    print(f"{entry.action_type.value}: User {entry.target_user_id}")
    print(f"Reason: {entry.reason}")
```

### Bulk Scanning

```python
result = automod.scan_messages_bulk(
    server_id=server.id,
    channel_id=channel.id,
    message_ids=[msg1.id, msg2.id, msg3.id]
)

print(f"Scanned: {result.total_scanned}")
print(f"Violations: {result.violations_found}")
print(f"Flagged messages: {result.messages_flagged}")
```

## Configuration

Add to `config/config.yaml`:

```yaml
automod:
  enabled: true
  default_actions:
    - delete_message
    - alert_moderators
  rate_limit_window: 60
  reputation_decay_rate: 1.0
  reputation_decay_interval: 86400
  max_violations_before_action: 3
  
  ai:
    openai:
      api_key: "sk-..."
      model: "text-moderation-latest"
      threshold: 0.5
    perspective:
      api_key: "..."
      threshold: 0.7
      attributes:
        - TOXICITY
        - SEVERE_TOXICITY
        - PROFANITY
    custom:
      endpoint_url: "https://your-api.com/moderate"
      api_key: "..."
      timeout_seconds: 10
```

## Integration with Messaging

The automod module can be integrated into the messaging flow:

```python
def send_message_with_automod(user_id, channel_id, content, server_id):
    result = automod.check_message(
        server_id=server_id,
        channel_id=channel_id,
        user_id=user_id,
        content=content
    )
    
    if not result.passed:
        for match in result.violations:
            automod.process_violation(
                server_id=server_id,
                channel_id=channel_id,
                user_id=user_id,
                message_id=None,
                match=match,
                actions=result.actions_to_take
            )
        raise ContentBlockedError("Message blocked by automod")
    
    return messaging.send_message(user_id, channel_id, content)
```

## Error Handling

```python
from src.core.automod import (
    AutoModError,
    RuleNotFoundError,
    RuleValidationError,
    AIBackendError,
    AIBackendUnavailableError,
)

try:
    automod.create_rule(...)
except RuleValidationError as e:
    print(f"Invalid config: {e.issues}")

try:
    automod.check_ai(content, backend="openai")
except AIBackendUnavailableError:
    print("OpenAI not configured")
except AIBackendError as e:
    print(f"AI error: {e.status_code}")
```

## Database Schema

Tables (prefixed with `automod_`):
- `automod_rules` - Rule definitions
- `automod_violations` - Violation records
- `automod_audit` - Audit log entries
- `automod_reputation` - User reputation scores
- `automod_exemptions` - Role/channel exemptions
- `automod_rate_tracking` - Rate limit tracking

## Testing

```bash
pytest src/tests/automod/ -v
pytest -m automod
```
