# AutoMod Module

Auto-moderation system for Plexichat supporting configurable rules, multiple action types, AI moderation backends, and user reputation scoring.

## Features

- Configurable rule engine with multiple rule types
- Multiple actions per rule (delete, timeout, kick, ban, alert)
- Two-phase pipeline: fast rules block sends synchronously; AI moderation runs async and replaces offending messages with flag-driven tombstones
- AI moderation backend support (local Shieldstral by default, plus OpenAI, Perspective API, and custom endpoints)
- Local-first AI: Shieldstral runs on your own hardware via llama-server, so message content never leaves your host
- Per-server rule configuration with enable/disable
- Exempt roles and channels
- Configurable thresholds and cooldowns
- Audit logging of all automod actions
- Real-time rule evaluation on message create/edit
- Admin-only audit-log link on AutoMod-removed message tombstones
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

AI rules default to the local **Shieldstral** backend (served in-process by
the profile-gated `shieldstral` compose service), so no content ever leaves
your server:

```python
result = automod.check_ai(content="Check this content")

if result.flagged:
    print(f"Flagged categories: {result.categories}")
    print(f"Scores: {result.scores}")
```

Other backends remain available and can be selected per rule:

```python
result = automod.check_ai(
    content="Check this content",
    backend="openai"
)
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

## Admin Management

AutoMod is administered from the system admin dashboard and API. AI credentials are stored in the global AutoMod config, not in per-server settings.

### Admin API

- `GET /api/v1/admin/automod/config` and `PUT /api/v1/admin/automod/config`
- `GET /api/v1/admin/automod/rules?server_id=...`
- `POST /api/v1/admin/automod/rules`
- `PATCH /api/v1/admin/automod/rules/{rule_id}`
- `DELETE /api/v1/admin/automod/rules/{rule_id}`

## Configuration

Add to `config/config.yaml` or set values via the admin API:

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
    shieldstral:
      endpoint_url: "http://shieldstral:8080"  # local llama-server (deploy --ai-automod)
      model: "shieldstral"
      threshold: 0.5
      policy_query: "Evaluate the following content for harmful, toxic, hateful, sexually explicit, or unsafe material. Answer with a single word only: 'yes' if it is harmful, 'no' if it is not."
      timeout_seconds: 60
    openai:
      api_key: "sk-PLACEHOLDER"  # pragma: allowlist secret
      model: "text-moderation-latest"
      threshold: 0.5
    perspective:
      api_key: "PLACEHOLDER"  # pragma: allowlist secret
      threshold: 0.7
      attributes:
        - TOXICITY
        - SEVERE_TOXICITY
        - PROFANITY
    custom:
      endpoint_url: "https://your-api.com/moderate"
      api_key: "PLACEHOLDER"  # pragma: allowlist secret
      timeout_seconds: 10
```

## Two-Phase Pipeline

Message moderation runs in two phases so that cheap rules never add latency and
expensive AI checks never block the send path:

### Phase 1 - Fast rules (synchronous, pre-broadcast)

Rules that do not use AI moderation (`KEYWORD`, `REGEX`, `MESSAGE_SPAM`,
`MENTION_SPAM`, `INVITE_LINKS`, `EXTERNAL_LINKS`, `CAPS_PERCENTAGE`,
`MASS_EMOJI`, `REPEATED_CHARS`) run **before** the message is created or
broadcast. A match blocks the send entirely - other clients never see the
message. Non-delete actions (timeout, kick, ban, alert, log) still execute via
`process_violation`, with `message_id=None` since the message was never created.

### Phase 2 - AI moderation (asynchronous, post-broadcast)

AI-moderation rules (`AI_MODERATION`) run **after** the message has been
broadcast, on a background thread. A match does not remove the message from the
database; instead the message content is blanked and an `automod_removed`
metadata flag is set, turning the message into a tombstone. A `MESSAGE_UPDATE`
event is broadcast so clients re-render it as "Message removed by Automod".
Rendering is **flag-driven** - a user typing those words is never affected.

Admins (and members with `server.automod` / `administrator`) see a
"View audit log" link on the tombstone, which opens the matching violation via
`GET /api/v1/servers/{server_id}/automod/violations/{violation_id}`.

## Integration with Messaging

The message send/edit routes implement the two-phase pipeline:

```python
from src.core import automod

# Phase 1: block the send before it happens
result = automod.check_message_fast(
    server_id=server_id,
    channel_id=channel_id,
    user_id=user_id,
    content=content,
)
if not result.passed:
    for match in result.violations:
        automod.process_violation(
            server_id=server_id,
            channel_id=channel_id,
            user_id=user_id,
            message_id=None,
            match=match,
            actions=result.actions_to_take,
        )
    raise ContentBlockedError("Message blocked by automod")

# ... message is created and broadcast ...

# Phase 2: schedule the async AI pass (returns immediately)
automod.schedule_ai_check(
    server_id=server_id,
    channel_id=channel_id,
    user_id=user_id,
    message_id=message_id,
    content=content,
)
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
    automod.check_ai(content)
except AIBackendUnavailableError:
    print("Shieldstral not configured")
except AIBackendError as e:
    print(f"AI error: {e.status_code}")
```

## Database Schema

Tables (prefixed with `automod_` and `srv_`):
- `automod_rules` - Rule definitions
- `automod_violations` - Violation records
- `automod_audit` - Audit log entries
- `automod_reputation` - User reputation scores
- `automod_exemptions` - Role/channel exemptions
- `automod_rate_tracking` - Rate limit tracking
- `srv_servers` - (Reference) Community servers

## Testing

```bash
pytest src/tests/automod/ -v
pytest -m automod
```
