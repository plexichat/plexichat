"""
Google Perspective API adapter.

Makes real HTTP calls to Perspective API for toxicity analysis.
"""

import json
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


class PerspectiveAdapter(BaseAIAdapter):
    """Adapter for Google Perspective API."""

    backend_type = AIBackendType.PERSPECTIVE

    API_URL = "https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze"

    CATEGORIES = {
        "TOXICITY": "Rude, disrespectful, or unreasonable content",
        "SEVERE_TOXICITY": "Very hateful, aggressive, or disrespectful content",
        "IDENTITY_ATTACK": "Negative or hateful content targeting identity",
        "INSULT": "Insulting or inflammatory content",
        "PROFANITY": "Swear words, curse words, or obscene language",
        "THREAT": "Intention to inflict pain, injury, or violence",
        "SEXUALLY_EXPLICIT": "References to sexual acts or body parts",
        "FLIRTATION": "Pickup lines, complimenting appearance, or suggestive content",
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._api_key = config.get("api_key", "")
        self._threshold = config.get("threshold", 0.7)
        self._requested_attributes = config.get(
            "attributes",
            [
                "TOXICITY",
                "SEVERE_TOXICITY",
                "IDENTITY_ATTACK",
                "INSULT",
                "PROFANITY",
                "THREAT",
            ],
        )
        self._language = config.get("language", "en")

    def check_content(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> AICheckResult:
        """Check content using Perspective API."""
        if not self.is_available():
            raise AIBackendUnavailableError(
                "Perspective API key not configured", backend="perspective"
            )

        try:
            attributes = {attr: {} for attr in self._requested_attributes}

            request_data = json.dumps(
                {
                    "comment": {"text": content},
                    "languages": [self._language],
                    "requestedAttributes": attributes,
                }
            ).encode("utf-8")

            url = f"{self.API_URL}?key={self._api_key}"
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise AIBackendError(
                    "Perspective API URL must be an absolute https URL",
                    backend="perspective",
                )

            request = Request(  # nosec B310
                url,
                data=request_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urlopen(request, timeout=self._timeout) as response:  # nosec: B310
                response_data = json.loads(response.read().decode("utf-8"))

            return self._parse_response(response_data)

        except HTTPError as e:
            logger.error(f"Perspective API HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"Perspective API error: {e.reason}",
                backend="perspective",
                status_code=e.code,
            )
        except URLError as e:
            if "timed out" in str(e.reason).lower():
                raise AIBackendTimeoutError(
                    "Perspective API request timed out", backend="perspective"
                )
            logger.error(f"Perspective API URL error: {e.reason}")
            raise AIBackendError(
                f"Perspective API connection error: {e.reason}", backend="perspective"
            )
        except json.JSONDecodeError as e:
            logger.error(f"Perspective API response parse error: {e}")
            raise AIBackendError(
                "Failed to parse Perspective API response", backend="perspective"
            )

    def _parse_response(self, response: Dict[str, Any]) -> AICheckResult:
        """Parse Perspective API response."""
        attribute_scores = response.get("attributeScores", {})

        categories = {}
        scores = {}

        for attr, data in attribute_scores.items():
            summary_score = data.get("summaryScore", {}).get("value", 0.0)
            scores[attr] = summary_score
            categories[attr] = summary_score >= self._threshold

        flagged = any(categories.values())

        return AICheckResult(
            flagged=flagged,
            categories=categories,
            scores=scores,
            backend=self.backend_type or AIBackendType.PERSPECTIVE,
            raw_response=response,
        )

    def is_available(self) -> bool:
        """Check if Perspective API is configured."""
        return bool(self._api_key)

    def get_categories(self) -> Dict[str, str]:
        """Get Perspective API categories."""
        return {
            cat: desc
            for cat, desc in self.CATEGORIES.items()
            if cat in self._requested_attributes
        }
