"""
OpenAI Moderation API adapter.

Makes real HTTP calls to OpenAI's moderation endpoint.
"""

import json
from typing import Dict, Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import utils.logger as logger

from .base import BaseAIAdapter
from ..models import AICheckResult, AIBackendType
from ..exceptions import AIBackendError, AIBackendUnavailableError, AIBackendTimeoutError


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
    
    def check_content(self, content: str, context: Optional[Dict[str, Any]] = None) -> AICheckResult:
        """Check content using OpenAI Moderation API."""
        if not self.is_available():
            raise AIBackendUnavailableError(
                "OpenAI API key not configured",
                backend="openai"
            )
        
        try:
            request_data = json.dumps({
                "input": content,
                "model": self._model
            }).encode("utf-8")
            
            request = Request(
                self._api_url,
                data=request_data,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            
            with urlopen(request, timeout=self._timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            
            return self._parse_response(response_data)
            
        except HTTPError as e:
            logger.error(f"OpenAI API HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"OpenAI API error: {e.reason}",
                backend="openai",
                status_code=e.code
            )
        except URLError as e:
            if "timed out" in str(e.reason).lower():
                raise AIBackendTimeoutError(
                    "OpenAI API request timed out",
                    backend="openai"
                )
            logger.error(f"OpenAI API URL error: {e.reason}")
            raise AIBackendError(
                f"OpenAI API connection error: {e.reason}",
                backend="openai"
            )
        except json.JSONDecodeError as e:
            logger.error(f"OpenAI API response parse error: {e}")
            raise AIBackendError(
                "Failed to parse OpenAI API response",
                backend="openai"
            )
    
    def _parse_response(self, response: Dict[str, Any]) -> AICheckResult:
        """Parse OpenAI moderation response."""
        results = response.get("results", [{}])[0]
        
        categories = results.get("categories", {})
        scores = results.get("category_scores", {})
        flagged = results.get("flagged", False)
        
        flagged_categories = {
            cat: flagged_val
            for cat, flagged_val in categories.items()
        }
        
        category_scores = {
            cat: score
            for cat, score in scores.items()
        }
        
        above_threshold = any(
            score >= self._threshold
            for score in category_scores.values()
        )
        
        return AICheckResult(
            flagged=flagged or above_threshold,
            categories=flagged_categories,
            scores=category_scores,
            backend=self.backend_type,
            raw_response=response
        )
    
    def is_available(self) -> bool:
        """Check if OpenAI API is configured."""
        return bool(self._api_key)
    
    def get_categories(self) -> Dict[str, str]:
        """Get OpenAI moderation categories."""
        return self.CATEGORIES.copy()
