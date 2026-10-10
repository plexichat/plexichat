# AI moderation

This directory contains AI-powered content moderation adapters. Use an adapter to classify message content and trigger AutoMod actions when the score exceeds a threshold.

## Backends

| File | Class | Backend value | Description |
| --- | --- | --- | --- |
| `shieldstral.py` | `ShieldstralAdapter` | `shieldstral` | Local `llama-server` serving the Shieldstral safety classifier. Default. Content never leaves the host. |
| `openai.py` | `OpenAIAdapter` | `openai` | OpenAI Moderation API (`https://api.openai.com/v1/moderations`) |
| `perspective.py` | `PerspectiveAdapter` | `perspective` | Google Perspective API |
| `custom.py` | `CustomAdapter` | `custom` | Any HTTP endpoint you control |
| `base.py` | `BaseAIAdapter` | — | Abstract base class for all adapters |

All adapters extend `BaseAIAdapter` (`src/core/automod/ai/base.py`).

## Before you begin

You need:

- AutoMod set up (`src/core/automod/README.md`).
- For Shieldstral: the `shieldstral` compose profile running (`deploy --ai-automod`), which starts `llama-server` with the Shieldstral model. Set `automod.ai.shieldstral.endpoint_url` to an absolute `http(s)://` URL.
- For OpenAI/Perspective/Custom: API keys and endpoint URLs in `config.yaml` under `automod.ai.<backend>`.

## Base class

`BaseAIAdapter` defines the interface every backend implements:

```python
from typing import Dict, Any, Optional
from src.core.automod.models import AICheckResult, AIBackendType

class BaseAIAdapter(ABC):
    backend_type: Optional[AIBackendType] = None

    def __init__(self, config: Dict[str, Any]):
        self._config = config
        self._timeout = config.get("timeout_seconds", 10)

    def check_content(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> AICheckResult:
        """Check content and return AICheckResult. Raise AIBackendError on failure."""
        ...

    def is_available(self) -> bool:
        """Return True if the backend is configured (endpoint URL or API key present)."""
        ...

    def get_categories(self) -> Dict[str, str]:
        """Return category ID -> description for this backend."""
        ...
```

Return type:

```python
@dataclass
class AICheckResult:
    flagged: bool              # Whether content violates the threshold
    categories: Dict[str, bool] # Category -> flagged
    scores: Dict[str, float]   # Category -> 0.0–1.0
    backend: AIBackendType
    raw_response: Dict[str, Any]
```

Exceptions (`src/core/automod/exceptions.py`):

| Exception | When it is raised |
| --- | --- |
| `AIBackendUnavailableError` | Backend not configured (`is_available()` is `False`) |
| `AIBackendError` | HTTP or parse error; includes `backend` and `status_code` |
| `AIBackendTimeoutError` | Request timed out |

## Use an adapter

### 1. Check content through AutoMod (recommended)

```python
from src.core import automod

result = automod.check_ai(content="Check this content")
if result.flagged:
    print(f"Flagged: {result.categories}")
    print(f"Scores: {result.scores}")
    print(f"Backend: {result.backend}")

# Override backend for one check
result = automod.check_ai(content="Check this content", backend="openai")
```

`check_ai` selects the adapter from `automod.ai.<backend>` config and calls `check_content`.

### 2. Use an adapter directly

```python
from src.core.automod.ai.shieldstral import ShieldstralAdapter

adapter = ShieldstralAdapter(config={
    "endpoint_url": "http://shieldstral:8080/v1/chat/completions",
    "model": "shieldstral",
    "threshold": 0.5,
    "timeout_seconds": 60,
})
if adapter.is_available():
    result = adapter.check_content("hello world")
    if result.flagged:
        print(result.scores)
```

```python
from src.core.automod.ai.openai import OpenAIAdapter

adapter = OpenAIAdapter(config={
    "api_key": "sk-...",
    "model": "text-moderation-latest",
    "threshold": 0.5,
    "timeout_seconds": 10,
})
result = adapter.check_content("hello world")
```

## Configure backends

Set values in `config/config.yaml` under `automod.ai`:

```yaml
automod:
  ai:
    shieldstral:
      endpoint_url: "http://shieldstral:8080/v1/chat/completions"
      api_key: ""  # Optional bearer token
      model: "shieldstral"
      threshold: 0.5
      policy_query: "Evaluate the following content for harmful, toxic, hateful, sexually explicit, or unsafe material. Answer with a single word only: 'yes' if it is harmful, 'no' if it is not."
      timeout_seconds: 60
    openai:
      api_key: "sk-PLACEHOLDER"  # pragma: allowlist secret
      api_url: "https://api.openai.com/v1/moderations"
      model: "text-moderation-latest"
      threshold: 0.5
      timeout_seconds: 10
    perspective:
      api_key: "PLACEHOLDER"  # pragma: allowlist secret
      threshold: 0.7
      attributes: [TOXICITY, SEVERE_TOXICITY, PROFANITY]
      timeout_seconds: 10
    custom:
      endpoint_url: "https://your-api.com/moderate"
      api_key: "PLACEHOLDER"  # pragma: allowlist secret
      timeout_seconds: 10
```

Shieldstral details:

- Talks to an OpenAI-compatible `/v1/chat/completions` endpoint. The adapter sends `{"model": ..., "messages": [{"role": "user", "content": "<policy_query> <content>"}]}` with `logprobs=True` and converts the first-token `yes`/`no` log-probabilities into a continuous score via `1 / (1 + exp(no_lp - yes_lp))`. Falls back to text parsing if log-probs are missing.
- Validates `endpoint_url` is an absolute `http(s)://` URL; otherwise raises `AIBackendError`.
- Exposes one category: `{"safety": "Overall safety score (0-1) from Shieldstral"}`.

OpenAI details:

- Sends `{"input": content, "model": ...}` to `api_url` with `Authorization: Bearer <api_key>`. Requires `https` URL.
- Parses `results[0].categories`, `category_scores`, and `flagged`. Flags if `flagged` is `true` or any `category_scores` value meets `threshold`.
- Categories include `hate`, `harassment`, `self-harm`, `sexual`, `violence`, and sub-categories (see `OpenAIAdapter.CATEGORIES`).

## Handle the two-phase pipeline

AI checks run asynchronously via `schedule_ai_check()`, which calls `check_ai_rules()` on a daemon thread. If flagged, the manager tombstones the message and broadcasts `MESSAGE_UPDATE`. See `src/core/automod/README.md` for the full pipeline and `fail_closed` behavior.

## Follow security best practices

- Keep Shieldstral on a private network; do not expose `llama-server` to the public internet.
- Store API keys in `config.yaml` or environment-injected secrets, not in per-server rule configs.
- Use `threshold` to tune sensitivity; start at `0.5` for Shieldstral/OpenAI and `0.7` for Perspective.

## Test the adapters

```bash
pytest src/tests/automod/test_ai_backends.py -v
pytest -m automod
```
