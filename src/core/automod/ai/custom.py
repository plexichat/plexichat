"""
Custom AI endpoint adapter - Connects to custom moderation API endpoints.
"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

import utils.logger as logger

from .base import BaseAIAdapter
from ..models import AICheckResult
from ..exceptions import AIBackendError


class CustomAdapter(BaseAIAdapter):
    """Adapter for custom AI moderation endpoints."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._endpoint_url = config.get("custom_endpoint_url", "")
        self._api_key = config.get("custom_api_key", "")
        self._auth_header = config.get("custom_auth_header", "Authorization")
        self._auth_prefix = config.get("custom_auth_prefix", "Bearer")
        self._request_format = config.get("custom_request_format", "json")
        self._content_field = config.get("custom_content_field", "content")
        self._flagged_field = config.get("custom_flagged_field", "flagged")
        self._categories_field = config.get("custom_categories_field", "categories")
        self._scores_field = config.get("custom_scores_field", "scores")
    
    def check_content(self, content: str, context: Optional[Dict[str, Any]] = None) -> AICheckResult:
        """Check content using custom endpoint."""
        if not self._endpoint_url:
            return AICheckResult(
                flagged=False,
                error="Custom endpoint URL not configured"
            )
        
        try:
            payload = {self._content_field: content}
            
            if context:
                payload["context"] = context
            
            data = json.dumps(payload).encode("utf-8")
            
            headers = {"Content-Type": "application/json"}
            
            if self._api_key:
                auth_value = f"{self._auth_prefix} {self._api_key}" if self._auth_prefix else self._api_key
                headers[self._auth_header] = auth_value
            
            req = urllib.request.Request(
                self._endpoint_url,
                data=data,
                headers=headers,
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            
            flagged = self._extract_field(response_data, self._flagged_field, False)
            categories = self._extract_field(response_data, self._categories_field, {})
            scores = self._extract_field(response_data, self._scores_field, {})
            
            if isinstance(flagged, str):
                flagged = flagged.lower() in ("true", "1", "yes")
            
            return AICheckResult(
                flagged=bool(flagged),
                categories=categories if isinstance(categories, dict) else {},
                scores=scores if isinstance(scores, dict) else {},
                raw_response=response_data
            )
            
        except urllib.error.HTTPError as e:
            logger.error(f"Custom endpoint HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"Custom endpoint error: {e.reason}",
                backend="custom",
                status_code=e.code
            )
        except urllib.error.URLError as e:
            logger.error(f"Custom endpoint connection error: {e.reason}")
            raise AIBackendError(
                f"Custom endpoint connection error: {e.reason}",
                backend="custom"
            )
        except json.JSONDecodeError as e:
            logger.error(f"Custom endpoint response parse error: {e}")
            raise AIBackendError(
                "Failed to parse custom endpoint response",
                backend="custom"
            )
        except Exception as e:
            logger.error(f"Custom moderation check failed: {e}")
            return AICheckResult(
                flagged=False,
                error=str(e)
            )
    
    def _extract_field(self, data: Dict, field_path: str, default: Any) -> Any:
        """Extract a field from response data using dot notation path."""
        if not field_path:
            return default
        
        parts = field_path.split(".")
        current = data
        
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        
        return current
    
    @classmethod
    def get_backend_name(cls) -> str:
        return "custom"
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> list:
        """Validate custom adapter configuration."""
        issues = []
        
        endpoint_url = config.get("custom_endpoint_url")
        if not endpoint_url:
            issues.append("custom_endpoint_url is required")
        elif not isinstance(endpoint_url, str):
            issues.append("custom_endpoint_url must be a string")
        elif not endpoint_url.startswith(("http://", "https://")):
            issues.append("custom_endpoint_url must be a valid HTTP(S) URL")
        
        for key in ["custom_api_key", "custom_auth_header", "custom_auth_prefix",
                    "custom_content_field", "custom_flagged_field",
                    "custom_categories_field", "custom_scores_field"]:
            value = config.get(key)
            if value is not None and not isinstance(value, str):
                issues.append(f"{key} must be a string")
        
        return issues
