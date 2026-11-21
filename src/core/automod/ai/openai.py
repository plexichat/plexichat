"""
OpenAI Moderation API adapter.

Makes real HTTP calls to OpenAI's moderation endpoint.
"""

import json
from typing import Dict, Any, Optional
from urllib.parse import urlparse
from urllib.request import Request

from src.utils.outbound import pinned_urlopen as _pinned_urlopen
from src.utils.security import URLValidator
from urllib.error import URLError, HTTPError

import utils.logger as logger

from .base import BaseAIAdapter
from ..models import AICheckResult, AIBackendType
from ..exceptions import (
    AIBackendError,
    AIBackendUnavailableError,
    AIBackendTimeoutError,
)


def urlopen(request: Request, timeout: float = 30):
    """Fetch the configured moderation endpoint through the SSRF gate.

    ``api_url`` is operator/admin config (``automod.ai.openai``). The shared
    ``urlopen`` transport enforces the scheme allowlist and refuses redirects,
    but it does not screen the destination IP: a loopback, link-local or private
    address (or a hostname that answers public to the admin-schema write-time
    check and private to the fetch-time resolver) reached the socket and
    received the configured API key. The admin schema gate is a write boundary
    only -- it neither pins the address nor covers config loaded from disk -- so
    the fetch site itself has to fail closed. The adapter's own https-only rule
    below still applies.

    Validate first (fail closed on a disallowed range or a failed resolution),
    then hand the *approved* IP to ``pinned_urlopen``, which connects to that IP
    while keeping the real hostname in ``Host`` / TLS SNI. Redirects are refused
    by the shared transport, so there is no second, unvalidated hop.
    """
    url = request.full_url if isinstance(request, Request) else str(request)
    try:
        URLValidator().validate_url_for_request(url)
    except ValueError as exc:
        raise AIBackendError(
            "OpenAI endpoint URL refused by SSRF validation", backend="openai"
        ) from exc
    headers = dict(request.header_items()) if isinstance(request, Request) else {}
    method = request.get_method() if isinstance(request, Request) else "GET"
    data = request.data if isinstance(request, Request) else None
    return _pinned_urlopen(
        url, timeout=timeout, method=method, data=data, headers=headers
    )


class OpenAIAdapter(BaseAIAdapter):
    """Adapter for OpenAI Moderation API."""

    backend_type = AIBackendType.OPENAI

    DEFAULT_API_URL = "https://api.openai.com/v1/moderations"

    CATEGORIES = {
        "hate": "Content that expresses hate toward a group",
        "hate/threatening": "Hateful content with threats of violence",
        "harassment": "Content that harasses an individual",
        "harassment/threatening": "Harassment with threats",
        "self-harm": "Content promoting self-harm",
        "self-harm/intent": "Content expressing intent to self-harm",
        "self-harm/instructions": "Instructions for self-harm",
        "sexual": "Sexual content",
        "sexual/minors": "Sexual content involving minors",
        "violence": "Violent content",
        "violence/graphic": "Graphic violent content",
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._api_key = config.get("api_key", "")
        self._api_url = config.get("api_url", self.DEFAULT_API_URL)
        self._model = config.get("model", "text-moderation-latest")
        self._threshold = config.get("threshold", 0.5)

    def check_content(
        self, content: str, context: Optional[Dict[str, Any]] = None
    ) -> AICheckResult:
        """Check content using OpenAI Moderation API."""
        if not self.is_available():
            raise AIBackendUnavailableError(
                "OpenAI API key not configured", backend="openai"
            )

        try:
            parsed = urlparse(self._api_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise AIBackendError(
                    "OpenAI API URL must be an absolute https URL",
                    backend="openai",
                )

            request_data = json.dumps({"input": content, "model": self._model}).encode(
                "utf-8"
            )

            request = Request(
                self._api_url,
                data=request_data,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            with urlopen(request, timeout=self._timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))

            return self._parse_response(response_data)

        except HTTPError as e:
            logger.error(f"OpenAI API HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"OpenAI API error: {e.reason}", backend="openai", status_code=e.code
            )
        except URLError as e:
            if "timed out" in str(e.reason).lower():
                raise AIBackendTimeoutError(
                    "OpenAI API request timed out", backend="openai"
                )
            logger.error(f"OpenAI API URL error: {e.reason}")
            raise AIBackendError(
                f"OpenAI API connection error: {e.reason}", backend="openai"
            )
        except json.JSONDecodeError as e:
            logger.error(f"OpenAI API response parse error: {e}")
            raise AIBackendError(
                "Failed to parse OpenAI API response", backend="openai"
            )

    def _parse_response(self, response: Dict[str, Any]) -> AICheckResult:
        """Parse OpenAI moderation response."""
        results = response.get("results", [{}])[0]

        categories = results.get("categories", {})
        scores = results.get("category_scores", {})
        flagged = results.get("flagged", False)

        flagged_categories = {
            cat: flagged_val for cat, flagged_val in categories.items()
        }

        category_scores = {cat: score for cat, score in scores.items()}

        above_threshold = any(
            score >= self._threshold for score in category_scores.values()
        )

        return AICheckResult(
            flagged=flagged or above_threshold,
            categories=flagged_categories,
            scores=category_scores,
            backend=self.backend_type or AIBackendType.OPENAI,
            raw_response=response,
        )

    def is_available(self) -> bool:
        """Check if OpenAI API is configured."""
        return bool(self._api_key)

    def get_categories(self) -> Dict[str, str]:
        """Get OpenAI moderation categories."""
        return self.CATEGORIES.copy()
