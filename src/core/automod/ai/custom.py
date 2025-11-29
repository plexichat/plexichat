"""
Custom AI endpoint adapter.

Makes real HTTP calls to user-configured moderation endpoints.
"""

import json
from typing import Dict, Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import utils.logger as logger

from .base import BaseAIAdapter
from ..models import AICheckResult, AIBackendType
from ..exceptions import AIBackendError, AIBackendUnavailableError, AIBackendTimeoutError


class CustomAdapter(BaseAIAdapter):
    """Adapter for custom moderation API endpoints."""
    
    backend_type = AIBackendType.CUSTOM
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._endpoint_url = config.get("endpoint_url", "")
        self._api_key = config.get("api_key", "")
        self._auth_header = config.get("auth_header", "Authorization")
        self._auth_prefix = config.get("auth_prefix", "Bearer")
        self._threshold = config.get("threshold", 0.5)
        self._request_format = config.get("request_format", "default")
        self._headers = config.get("headers", {})
    
    def check_content(self, content: str, context: Optional[Dict[str, Any]] = None) -> AICheckResult:
        """Check content using custom endpoint."""
        if not self.is_available():
            raise AIBackendUnavailableError(
                "Custom endpoint URL not configured",
                backend="custom"
            )
        
        try:
            request_data = self._build_request(content, context)
            headers = self._build_headers()
            
            request = Request(
                self._endpoint_url,
                data=json.dumps(request_data).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            
            with urlopen(request, timeout=self._timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            
            return self._parse_response(response_data)
            
        except HTTPError as e:
            logger.error(f"Custom API HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"Custom API error: {e.reason}",
                backend="custom",
                status_code=e.code
            )
        except URLError as e:
            if "timed out" in str(e.reason).lower():
                raise AIBackendTimeoutError(
                    "Custom API request timed out",
                    backend="custom"
                )
            logger.error(f"Custom API URL error: {e.reason}")
            raise AIBackendError(
                f"Custom API connection error: {e.reason}",
                backend="custom"
            )
        except json.JSONDecodeError as e:
            logger.error(f"Custom API response parse error: {e}")
            raise AIBackendError(
                "Failed to parse custom API response",
                backend="custom"
            )
    
    def _build_request(self, content: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Build request payload based on configured format."""
        if self._request_format == "openai":
            return {"input": content}
        elif self._request_format == "perspective":
            return {"comment": {"text": content}}
        else:
            return {
                "content": content,
                "context": context or {}
            }
    
    def _build_headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        headers.update(self._headers)
        
        if self._api_key:
            auth_value = f"{self._auth_prefix} {self._api_key}" if self._auth_prefix else self._api_key
            headers[self._auth_header] = auth_value
        
        return headers
    
    def _parse_response(self, response: Dict[str, Any]) -> AICheckResult:
        """Parse custom endpoint response."""
        flagged = response.get("flagged", False)
        
        if not flagged and "score" in response:
            flagged = response["score"] >= self._threshold
        
        if not flagged and "scores" in response:
            scores = response["scores"]
            if isinstance(scores, dict):
                flagged = any(s >= self._threshold for s in scores.values())
        
        categories = response.get("categories", {})
        if isinstance(categories, list):
            categories = {cat: True for cat in categories}
        
        scores = response.get("scores", {})
        if isinstance(scores, (int, float)):
            scores = {"default": float(scores)}
        elif isinstance(scores, dict):
            scores = {k: float(v) for k, v in scores.items()}
        
        return AICheckResult(
            flagged=flagged,
            categories=categories,
            scores=scores,
            backend=self.backend_type or AIBackendType.CUSTOM,
            raw_response=response
        )
    
    def is_available(self) -> bool:
        """Check if custom endpoint is configured."""
        return bool(self._endpoint_url)
    
    def get_categories(self) -> Dict[str, str]:
        """Custom endpoints define their own categories."""
        return {}
