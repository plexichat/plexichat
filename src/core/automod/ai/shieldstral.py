"""
Shieldstral local AI adapter.

Talks to a local llama-server instance serving the Shieldstral-1.0-3B
policy-adaptive safety classifier (Apache-2.0) over the OpenAI-compatible
``/v1/chat/completions`` endpoint. The model answers a natural-language
policy query with a single ``yes`` / ``no`` token; the adapter requests
token log-probabilities and converts them into a continuous safety score,
so thresholds behave like the other scoring backends.

Everything stays on your own infrastructure - no content ever leaves the
host. The model + vision projector are served by the profile-gated
``shieldstral`` compose service (see plexichat/docker-compose.yml).
"""

import json
from math import exp, inf
from typing import Dict, Any, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import utils.logger as logger

from .base import BaseAIAdapter
from ..models import AICheckResult, AIBackendType
from ..exceptions import (
    AIBackendError,
    AIBackendUnavailableError,
    AIBackendTimeoutError,
)

DEFAULT_POLICY_QUERY = (
    "Evaluate the following content for harmful, toxic, hateful, sexually "
    "explicit, or unsafe material. Answer with a single word only: 'yes' if "
    "it is harmful, 'no' if it is not."
)


class ShieldstralAdapter(BaseAIAdapter):
    """Adapter for a local Shieldstral llama-server endpoint."""

    backend_type = AIBackendType.SHIELDSTRAL

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._endpoint_url = config.get("endpoint_url", "")
        self._api_key = config.get("api_key", "")
        self._model = config.get("model", "shieldstral")
        self._threshold = config.get("threshold", 0.5)
        self._policy_query = config.get("policy_query", DEFAULT_POLICY_QUERY)
        self._yes_token = (config.get("yes_token", "yes") or "yes").lower()
        self._no_token = (config.get("no_token", "no") or "no").lower()

    def check_content(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> AICheckResult:
        """Check content using the local Shieldstral endpoint."""
        if not self.is_available():
            raise AIBackendUnavailableError(
                "Shieldstral endpoint URL not configured", backend="shieldstral"
            )

        try:
            parsed = urlparse(self._endpoint_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise AIBackendError(
                    "Shieldstral endpoint URL must be an absolute http(s) URL",
                    backend="shieldstral",
                )

            request_data = {
                "model": self._model,
                "messages": [
                    {"role": "user", "content": f"{self._policy_query} {content}"}
                ],
                "logprobs": True,
                "top_logprobs": 2,
                "max_tokens": 8,
                "temperature": 0.0,
            }

            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            request = Request(
                self._endpoint_url,
                data=json.dumps(request_data).encode("utf-8"),
                headers=headers,
                method="POST",  # nosec B310
            )

            with urlopen(request, timeout=self._timeout) as response:  # nosec: B310
                response_data = json.loads(response.read().decode("utf-8"))

            return self._parse_response(response_data)

        except HTTPError as e:
            logger.error(f"Shieldstral API HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"Shieldstral API error: {e.reason}",
                backend="shieldstral",
                status_code=e.code,
            )
        except URLError as e:
            if "timed out" in str(e.reason).lower():
                raise AIBackendTimeoutError(
                    "Shieldstral API request timed out", backend="shieldstral"
                )
            logger.error(f"Shieldstral API URL error: {e.reason}")
            raise AIBackendError(
                f"Shieldstral API connection error: {e.reason}", backend="shieldstral"
            )
        except json.JSONDecodeError as e:
            logger.error(f"Shieldstral API response parse error: {e}")
            raise AIBackendError(
                "Failed to parse Shieldstral API response", backend="shieldstral"
            )

    def _parse_response(self, response: Dict[str, Any]) -> AICheckResult:
        """Parse a Shieldstral chat-completions response into a score."""
        answer = ""
        try:
            answer = (
                (response.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        except (AttributeError, TypeError, IndexError, KeyError):
            answer = ""

        score = self._score_from_logprobs(response)
        if score is None:
            score = self._score_from_text(answer)

        flagged = score >= self._threshold

        return AICheckResult(
            flagged=flagged,
            categories={"safety": flagged},
            scores={"safety": round(score, 6)},
            backend=self.backend_type or AIBackendType.SHIELDSTRAL,
            raw_response=response,
        )

    def _score_from_logprobs(self, response: Dict[str, Any]) -> Optional[float]:
        """Derive a yes/no probability from the first-token log-probs."""
        try:
            logprobs = (response.get("choices") or [{}])[0].get("logprobs") or {}
            top = (logprobs.get("content") or [{}])[0].get("top_logprobs") or []
        except (AttributeError, TypeError, IndexError):
            return None

        yes_lp = -inf
        no_lp = -inf

        for item in top:
            try:
                token = item.get("token")
                lp = item.get("logprob")
            except (AttributeError, TypeError):
                continue
            if token is None or lp is None:
                continue
            token_lower = str(token).strip().lower()
            if token_lower.startswith(self._yes_token):
                yes_lp = max(yes_lp, float(lp))
            elif token_lower.startswith(self._no_token):
                no_lp = max(no_lp, float(lp))

        if yes_lp == -inf and no_lp == -inf:
            return None

        if yes_lp == -inf:
            return 0.0
        if no_lp == -inf:
            return 1.0

        return 1.0 / (1.0 + exp(no_lp - yes_lp))

    def _score_from_text(self, answer: str) -> float:
        """Fall back to the raw ``yes``/``no`` answer when no log-probs."""
        normalized = (answer or "").strip().lower()
        if normalized.startswith(self._yes_token):
            return 1.0
        if normalized.startswith(self._no_token):
            return 0.0
        logger.warning(
            f"Shieldstral returned an unparseable answer: {answer!r}. "
            "Treating as safe (score 0)."
        )
        return 0.0

    def is_available(self) -> bool:
        """Check if the local endpoint is configured."""
        return bool(self._endpoint_url)

    def get_categories(self) -> Dict[str, str]:
        """Shieldstral exposes a single safety score."""
        return {"safety": "Overall safety score (0-1) from Shieldstral"}
