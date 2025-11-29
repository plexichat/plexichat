"""
OpenAI moderation adapter - Uses OpenAI's moderation API.
"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

import utils.logger as logger

from .base import BaseAIAdapter
from ..models import AICheckResult
from ..exceptions import AIBackendError, AIConfigurationError


class OpenAIAdapter(BaseAIAdapter):
    """Adapter for OpenAI's moderation API."""
    
    API_URL = "https://api.openai.com/v1/moderations"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._api_key = config.get("openai_api_key", "")
        self._model = config.get("openai_model", "text-moderation-latest")
    
    def check_content(self, content: str, context: Optional[Dict[str, Any]] = None) -> AICheckResult:
        """Check content using OpenAI moderation API."""
        if not self._api_key:
            return AICheckResult(
                flagged=False,
                error="OpenAI API key not configured"
            )
        
        try:
            payload = {
                "input": content,
                "model": self._model,
            }
            
            data = json.dumps(payload).encode("utf-8")
            
            req = urllib.request.Request(
                self.API_URL,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            
            if not response_data.get("results"):
                return AICheckResult(
                    flagged=False,
                    error="Empty response from OpenAI"
                )
            
            result = response_data["results"][0]
            
            categories = {}
            scores = {}
            
            for category, flagged in result.get("categories", {}).items():
                categories[category] = flagged
            
            for category, score in result.get("category_scores", {}).items():
                scores[category] = score
            
            return AICheckResult(
                flagged=result.get("flagged", False),
                categories=categories,
                scores=scores,
                raw_response=response_data
            )
            
        except urllib.error.HTTPError as e:
            logger.error(f"OpenAI API HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"OpenAI API error: {e.reason}",
                backend="openai",
                status_code=e.code
            )
        except urllib.error.URLError as e:
            logger.error(f"OpenAI API connection error: {e.reason}")
            raise AIBackendError(
                f"OpenAI API connection error: {e.reason}",
                backend="openai"
            )
        except json.JSONDecodeError as e:
            logger.error(f"OpenAI API response parse error: {e}")
            raise AIBackendError(
                "Failed to parse OpenAI response",
                backend="openai"
            )
        except Exception as e:
            logger.error(f"OpenAI moderation check failed: {e}")
            return AICheckResult(
                flagged=False,
                error=str(e)
            )
    
    @classmethod
    def get_backend_name(cls) -> str:
        return "openai"
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> list:
        """Validate OpenAI adapter configuration."""
        issues = []
        
        api_key = config.get("openai_api_key")
        if not api_key:
            issues.append("openai_api_key is required")
        elif not isinstance(api_key, str):
            issues.append("openai_api_key must be a string")
        elif not api_key.startswith("sk-"):
            issues.append("openai_api_key appears to be invalid (should start with 'sk-')")
        
        model = config.get("openai_model")
        if model is not None and not isinstance(model, str):
            issues.append("openai_model must be a string")
        
        return issues
