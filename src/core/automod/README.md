# AutoMod

AutoMod is the auto-moderation system for Plexichat. Use it to define rules, run a two-phase moderation pipeline, score user reputation, and log all actions for audit. It supports fast local rules and AI backends, including a local Shieldstral model that keeps content on your host.

## Features

- Rule engine with 10 rule types (`KEYWORD`, `REGEX`, `MESSAGE_SPAM`, `MENTION_SPAM`, `INVITE_LINKS`, `EXTERNAL_LINKS`, `CAPS_PERCENTAGE`, `MASS_EMOJI`, `REPEATED_CHARS`, `AI_MODERATION`)
- Multiple actions per rule (`DELETE_MESSAGE`, `TIMEOUT_USER`, `KICK_USER`, `BAN_USER`, `ALERT_MODERATORS`, `LOG_ONLY`)
- Two-phase pipeline: fast rules block synchronously before broadcast, AI rules run asynchronously after broadcast and create flag-driven tombstones
- AI backends: Shieldstral (local, default), OpenAI, Perspective API, and custom endpoints
- Per-server rule configuration with enable/disable and priority
- Exemptions by role and channel, per rule or globally
- Configurable thresholds, cooldowns, and reputation decay
- Audit logging for every action and violation
- Real-time evaluation on message create and edit
- Admin-only audit-log link on AI-removed tombstones
- Bulk scanning for raids and reputation scoring

## Before you begin

You need a connected `Database`, plus `auth`, `messaging`, `servers`, and `notifications` modules if you want kicks, bans, deletes, and alerts to execute fully. Install the `shieldstral` compose profile if you plan to use local AI (`deploy --ai-automod`).

Infrastructure defaults that affect AutoMod:

| Component | Default | Source |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | `src/config_defaults.py:54` |
| `api.api_prefix` | `/api/v1` | `src/config_defaults.py:131` |
| WebSocket gateway path | `/gateway` (full: `/api/v1` prefix not applied; mount is `/gateway`) | `src/api/websocket/gateway.py:261` |
| Health ready probe | `GET /api/v1/ready` | `src/api/routes/health.py:18` |
| Health detailed check | `GET /api/v1/health` | `src/api/routes/health.py:47` |
| TLS | Disabled by default (`tls.enabled: false`) | `src/config_defaults.py:599` |
| Voice SFU backend | `aiortc` | `src/config_defaults.py:399` |
| Database | PostgreSQL 18 (production) / SQLite (dev), Valkey 9.1.1 for presence/cache | `docker-compose.yml`, `src/config_defaults.py` |
| Python | 3.14 | `Dockerfile`, `pyproject.toml` |
| Rate limiting global | 25,000 req/60s burst 5,000 | `src/config_defaults.py:273` |
| Rate limiting user | 10,000 req/60s burst 2,500 | `src/config_defaults.py:273` |
| Rate limiting IP | 5,000 req/60s burst 1,500 | `src/config_defaults.py:273` |
| Gateway opcodes | `50` (identify), `60–67` (voice) | `src/api/websocket/opcodes.py` |
| Gateway intents | `20`, `21` | `src/api/websocket/intents.py` |

## Set up AutoMod

Call `setup()` once at startup:

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

`setup()` creates `AutoModManager`, creates tables (`automod_rules`, `automod_violations`, `automod_audit`, `automod_reputation`, `automod_exemptions`, `automod_rate_tracking`), and loads `config.yaml` under `automod`.

To reload config without restarting, call `automod.reload_config()`.

## Check messages

### 1. Run fast checks before broadcast

Use `check_message_fast()` synchronously in the send path. If it fails, you block delivery:

```python
from src.core import automod

result = automod.check_message_fast(
    server_id=server.id,
    channel_id=channel.id,
    user_id=user.id,
    content="Check this message content",
)
if not result.passed:
    for v in result.violations:
        print(f"Rule {v.rule_type.value} matched: {v.matched_content}")
    if result.should_delete:
        print("Message should be deleted")
```

### 2. Run all rules (fast + AI) synchronously

`check_message()` runs every enabled rule at once. Prefer the two-phase API in production so AI does not add latency.

```python
result = automod.check_message(
    server_id=server.id,
    channel_id=channel.id,
    user_id=user.id,
    content="Check this content",
)
```

### 3. Run AI rules asynchronously after broadcast

`schedule_ai_check()` starts a daemon thread and returns immediately. If an AI rule matches, it blanks the message content, sets `automod_removed` metadata, and broadcasts `MESSAGE_UPDATE` so clients re-render a tombstone. Rendering is flag-driven — typing the words `Message removed by Automod` does not trigger the UI.

```python
automod.schedule_ai_check(
    server_id=server.id,
    channel_id=channel.id,
    user_id=user.id,
    message_id=message.id,
    content=content,
)
```

Admins and members with `server.automod` or `administrator` see a `View audit log` link on the tombstone that opens `GET /api/v1/servers/{server_id}/automod/violations/{violation_id}`.

## Create and manage rules

### Rule types

| Type | Description | Key config options |
| --- | --- | --- |
| `KEYWORD` | Block specific keywords | `keywords`, `case_sensitive`, `whole_word`, `severity_map` |
| `REGEX` | Match regex patterns | `patterns` (each with `pattern`, `severity`, `name`) |
| `MESSAGE_SPAM` | Rate-limit messages | `max_messages`, `window_seconds`, `duplicate_threshold` |
| `MENTION_SPAM` | Limit mentions | `max_user_mentions`, `max_role_mentions`, `block_everyone` |
| `INVITE_LINKS` | Block server invites | `block_all`, `allowed_codes`, `code_length` |
| `EXTERNAL_LINKS` | Filter URLs | `mode` (`whitelist` or `blacklist`), `whitelist`, `blacklist` |
| `CAPS_PERCENTAGE` | Limit caps | `max_percentage`, `min_length` |
| `MASS_EMOJI` | Limit emoji | `max_emoji`, `max_percentage` |
| `REPEATED_CHARS` | Limit repeated characters | `max_repeats`, `min_occurrences` |
| `AI_MODERATION` | AI classification | `backend` (`shieldstral`, `openai`, `perspective`, `custom`), `threshold`, `categories` |

### Action types

| Action | Description | Options |
| --- | --- | --- |
| `DELETE_MESSAGE` | Delete the message | — |
| `TIMEOUT_USER` | Temporarily mute the user | `duration_seconds` |
| `KICK_USER` | Remove the user from the server | `reason` |
| `BAN_USER` | Permanently ban the user | `reason`, `delete_message_days` |
| `ALERT_MODERATORS` | Notify moderators | — |
| `LOG_ONLY` | Log only, take no action | — |

Each action extends `BaseAction` (`src/core/automod/actions/base.py`) and implements `execute(action, violation, context) -> bool` and `can_execute(action, violation, context) -> tuple`.

### Create a rule

```python
rule = automod.create_rule(
    user_id=admin.id,
    server_id=server.id,
    name="Block bad words",
    rule_type=automod.RuleType.KEYWORD,
    rule_config={
        "keywords": ["spam", "scam"],
        "case_sensitive": False,
        "whole_word": True,
    },
    actions=[
        {"action_type": "delete_message"},
        {"action_type": "alert_moderators"},
    ],
    exempt_roles=[mod_role.id],
    priority=10,
)
```

Use `channel_type` (not `type`) when you create channels for testing rules. The schema is `ChannelCreateRequest.channel_type` (`src/api/schemas/servers.py:85`) with values `text`, `voice`, `category`.

### Update, enable, and delete rules

```python
rule = automod.update_rule(user_id=admin.id, rule_id=rule.id, priority=20)
rule = automod.set_rule_enabled(user_id=admin.id, rule_id=rule.id, enabled=False)
automod.delete_rule(user_id=admin.id, rule_id=rule.id)
rules = automod.get_server_rules(server.id)
```

## Configure exemptions

Exempt a role or channel from all rules or from one rule:

```python
# Exempt a role from all rules
automod.add_exemption(
    user_id=admin.id,
    server_id=server.id,
    target_type="role",
    target_id=trusted_role.id,
    rule_id=None,
)
# Exempt a channel from one rule
automod.add_exemption(
    user_id=admin.id,
    server_id=server.id,
    target_type="channel",
    target_id=bot_channel.id,
    rule_id=rule.id,
)
automod.remove_exemption(user_id=admin.id, exemption_id=exemption.id)
```

## Check reputation and violations

```python
reputation = automod.get_user_reputation(user.id, server.id)
print(f"Score: {reputation.score}/100, violations: {reputation.violation_count}")

# Apply decay to all servers or one server
automod.decay_reputation(server.id)

violations = automod.get_violations(server.id, user_id=user.id, limit=50)
v = automod.get_violation(violation_id=123, server_id=server.id)
```

`MessageResponse.read_by` is `List[ReaderInfo]` (`src/api/schemas/messages.py:94`), where each `ReaderInfo` has `id`, `username`, and `avatar_url`. Do not treat it as a list of IDs.

## Use AI moderation

Shieldstral is the default. It runs on your hardware via `llama-server`, so content never leaves the host.

```python
result = automod.check_ai(content="Check this content")
if result.flagged:
    print(f"Flagged categories: {result.categories}")
    print(f"Scores: {result.scores}")
    print(f"Backend: {result.backend}")

# Use a different backend for one check
result = automod.check_ai(content="Check this content", backend="openai")
```

Supported backends: `shieldstral`, `openai`, `perspective`, `custom`. Each adapter extends `BaseAIAdapter` (`src/core/automod/ai/base.py`) and implements `check_content(content, context)` and `is_available()`. See `src/core/automod/ai/README.md`.

## Review the audit log

```python
entries = automod.get_audit_log(server.id, limit=50)
for entry in entries:
    print(f"{entry.action_type.value}: user {entry.target_user_id}, reason: {entry.reason}")
```

Filter by action type:

```python
entries = automod.get_audit_log(server.id, action_type=automod.ActionType.BAN_USER)
```

## Scan messages in bulk

Use bulk scanning to handle raids. The messages API also exposes bulk operations:

- `POST /api/v1/channels/{channel_id}/messages/bulk-delete` with `{"message_ids": [...]}` (max 100, `src/api/schemas/messages.py:61`)
- `POST /api/v1/channels/{channel_id}/messages/bulk-ack` and settings bulk endpoints at `src/api/routes/messages/` and `src/api/routes/settings/`

```python
result = automod.scan_messages_bulk(
    server_id=server.id,
    channel_id=channel.id,
    message_ids=[msg1.id, msg2.id, msg3.id],
)
print(f"Scanned: {result.total_scanned}, violations: {result.violations_found}, flagged: {result.messages_flagged}")
```

Other bulk-adjacent endpoints you may need during moderation workflows:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/media/proxy/{message_id}/{attachment_id}` | Authenticated media proxy (465) |
| `GET/POST` | `/api/v1/channels/{channel_id}/webhooks` | Webhooks (59) |
| `PATCH` | `/api/v1/users/@me/settings/bulk` | Bulk settings update (147) |

## Handle the two-phase pipeline

You implement the pipeline in the message send and edit routes:

1. Run `check_message_fast()` before you create the message. If `not result.passed`, you call `process_violation()` for each match with `message_id=None` (the message was never created) and raise `ContentBlockedError` to block the send.
2. Create and broadcast the message.
3. Call `schedule_ai_check()` to run `check_ai_rules()` on a background thread. If it matches, the manager blanks the content, sets the `automod_removed` flag, and broadcasts `MESSAGE_UPDATE`.

```python
from src.core import automod

# Phase 1: fast rules (synchronous, pre-broadcast)
result = automod.check_message_fast(
    server_id=server_id, channel_id=channel_id, user_id=user_id, content=content,
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

# ... create and broadcast message ...

# Phase 2: AI moderation (asynchronous, post-broadcast)
automod.schedule_ai_check(
    server_id=server_id, channel_id=channel_id, user_id=user_id, message_id=message_id, content=content,
)
```

If either phase raises internally and `automod.fail_closed` is `true` (the default), you must block or quarantine delivery and call `automod.record_internal_error()` so moderators can audit the outage. The module also writes an `internal_error` violation row.

Presence gating affects who sees moderation events: `GET /api/v1/users/{user_id}/presence` returns `404` when the viewer is not the user, a friend, or a shared server member (`src/api/routes/presence.py:379`). AutoMod broadcasts respect the same membership checks.

## Manage AutoMod as an admin

AutoMod is administered from the system admin dashboard and API. Store AI credentials in the global `automod` config, not in per-server settings.

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/v1/admin/automod/config` | Get global config |
| `PUT` | `/api/v1/admin/automod/config` | Update global config |
| `GET` | `/api/v1/admin/automod/rules?server_id=...` | List rules for a server |
| `POST` | `/api/v1/admin/automod/rules` | Create a rule |
| `PATCH` | `/api/v1/admin/automod/rules/{rule_id}` | Update a rule |
| `DELETE` | `/api/v1/admin/automod/rules/{rule_id}` | Delete a rule |

## Configure AutoMod

Set values in `config/config.yaml` or via the admin API:

```yaml
automod:
  enabled: true
  fail_closed: true  # Block delivery when moderation errors internally
  default_actions:
    - delete_message
    - alert_moderators
  rate_limit_window: 60
  reputation_decay_rate: 1.0
  reputation_decay_interval: 86400
  max_violations_before_action: 3

  ai:
    shieldstral:
      endpoint_url: "http://shieldstral:8080"  # Local llama-server (deploy --ai-automod)
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

## Handle errors

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

Role permission errors: creating or updating a role with wildcard permissions such as `*` or `messages.*` raises `InvalidPermissionKey` and the API returns `400` (`src/api/routes/servers/roles.py:201`). Scopes are defined in `src/core/auth/permissions.py:42`.

## Database schema

Tables (all prefixed with `automod_` or `srv_` for servers):

- `automod_rules` — Rule definitions
- `automod_violations` — Violation records, including `internal_error` rows
- `automod_audit` — Audit log entries
- `automod_reputation` — User reputation scores
- `automod_exemptions` — Role and channel exemptions
- `automod_rate_tracking` — Rate-limit tracking for spam rules
- `srv_servers` — Referenced community servers

## Test AutoMod

```bash
pytest src/tests/automod/ -v
pytest -m automod
```
