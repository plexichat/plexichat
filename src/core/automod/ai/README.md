# AI Moderation

AI-powered content moderation adapters.

## Backends

- `openai.py` - OpenAI moderation API
- `perspective.py` - Google Perspective API
- `custom.py` - Custom model endpoint adapter

## Usage

```python
from src.core.automod.ai import OpenAIAdapter, PerspectiveAdapter

adapter = OpenAIAdapter(api_key="...")
result = await adapter.analyze(content)
if result.is_toxic:
    # Take action
```

## Base Class

All adapters extend `BaseAIAdapter` which defines the analysis interface.
