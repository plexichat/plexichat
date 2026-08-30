# AutoMod rules

## Overview

This package provides content-filtering rules for automated moderation. Each rule implements a `matches()` check that the `AutoModManager` evaluates per message.

Use these rules through `AutoModManager` — see `plexichat/src/core/automod/manager/base.py:51` — rather than instantiating them directly in most cases.

Related platform endpoints:

*   WebSocket gateway: ` /gateway` — see `plexichat/src/api/websocket/gateway.py:261`. Use `/gateway`, not `/ws`.
*   Readiness probe: `GET /api/v1/ready` — see `plexichat/src/api/routes/health.py:18`.
*   Deep health check: `GET /api/v1/health` — see `plexichat/src/api/routes/health.py:46`.

## Prerequisites

*   Python 3.14.
*   The `src.core.automod` package.

## Rules

| File | Rule | Description |
| --- | --- | --- |
| `keyword.py` | `KeywordRule` | Blocks messages that contain configured keywords or phrases. |
| `regex.py` | `RegexRule` | Blocks messages that match regular expressions. |
| `spam.py` | `MessageSpamRule` — `RuleType.MESSAGE_SPAM` | Detects rapid messaging. |
| `mentions.py` | `MentionSpamRule` — `RuleType.MENTION_SPAM` | Detects excessive `@mentions`. |
| `links.py` | `InviteLinkRule` (`INVITE_LINKS`), `ExternalLinkRule` (`EXTERNAL_LINKS`) | Filters invite links and external URLs. |
| `caps.py` | `CapsPercentageRule` | Flags messages with excessive capitalization. |
| `emoji.py` | `MassEmojiRule` | Flags messages with excessive emoji. |
| `repeated.py` | `RepeatedCharsRule` | Flags messages with repeated characters. |
| `ai.py` | `AIModerationRule` — `RuleType.AI_MODERATION` | Delegates to AI adapters (OpenAI, Perspective, custom, Shieldstral). |

All rules extend `BaseRule` — see `plexichat/src/core/automod/rules/base.py` — which defines the `matches()` interface.

## Usage

### Use a rule directly

```python
from src.core.automod.rules import KeywordRule

rule = KeywordRule(keywords=["badword"])
if rule.matches(message):
    # Trigger the configured action
    pass
```

### Use rules through the manager

```python
from src.core.automod.manager import AutoModManager

am = AutoModManager(db)
am.ensure_default_rules(server_id=1, owner_id=1)

result = am.check_message(
    message_id=42,
    server_id=1,
    channel_id=10,
    user_id=2,
    content="hello world",
)
```

## Base class

All rules extend `BaseRule`:

*   `BaseRule.matches(message) -> bool` — return `True` if the message violates the rule.

The manager catches per-rule exceptions during evaluation and continues to the next rule.

## Configuration

Rule behavior is configured per server through `AutoModManager`. Global defaults live in `plexichat/src/config_defaults.py:668`:

```yaml
automod:
  enabled: true
  rules:
    caps:
      enabled: true
      max_percentage: 70.0
      min_length: 10
      ignore_commands: true
```

For server binding and API routing, use the correct global keys — see `plexichat/src/config_defaults.py`:

*   `server.host`, `server.port`, `server.reload` — see `plexichat/src/config_defaults.py:54`.
*   `api.api_prefix` — default `/api/v1` — see `plexichat/src/config_defaults.py:131`.
*   `tls` — see `plexichat/src/config_defaults.py:599`.

## Gateway opcodes and intents

If you forward rule violations to real-time clients, use the correct gateway values:

*   Opcodes — see `plexichat/src/api/websocket/opcodes.py:40`: `50 RATCHET_UPDATE`, `60 ARTIFACT_SUBSCRIBE`, `61 ARTIFACT_UNSUBSCRIBE`, `62 ARTIFACT_OP`, `63 ARTIFACT_SYNC`, `64 SUM_SUBSCRIBE`, `65 SUM_UNSUBSCRIBE`, `66 SUM_OP`, `67 SUM_SYNC`.
*   Intents — see `plexichat/src/core/events/types.py:120`: `1<<20 ARTIFACTS` and `1<<21 SUMS`.
