# Automod Rules

Content filtering rules for automated moderation.

## Rules

- `keyword.py` - Keyword/phrase blocking
- `regex.py` - Regular expression patterns
- `spam.py` - Message spam detection
- `mentions.py` - Mention spam detection
- `links.py` - Invite and external link filtering
- `caps.py` - Excessive caps detection
- `emoji.py` - Mass emoji detection
- `repeated.py` - Repeated character detection

## Usage

```python
from src.core.automod.rules import KeywordRule, SpamRule

rule = KeywordRule(keywords=["badword"])
if rule.matches(message):
    # Trigger action
```

## Base Class

All rules extend `BaseRule` which defines the `matches()` interface.
