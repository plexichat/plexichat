"""
Perspective API adapter - Uses Google's Perspective API for toxicity detection.
"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

import utils.logger as logger

from .base import BaseAIAdapter
from ..models import AICheckResult
from ..exceptions import AIBackendError


class PerspectiveAdapter(BaseAIAdapter):
    """Adapter for Google's Perspective API."""
    
    API_URL = "https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze"
    
    DEFAULT_ATTRIBUTES = [
        "TOXICITY",
        "SEVERE_TOXICITY",
        "IDENTITY_ATTACK",
        "INSULT",
        "PROFANITY",
        "THREAT",
    ]
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._api_key = config.get("perspective_api_key", "")
        self._threshold = config.get("perspective_threshold", 0.7)
        self._attributes = config.get("perspective_attributes", self.DEFAULT_ATTRIBUTES)
        self._language = config.get("perspective_language", "en")
    
    def check_content(self, content: str, context: Optional[Dict[str, Any]] = None) -> AICheckResult:
        """Check content using Perspective API."""
        if not self._api_key:
            return AICheckResult(
                flagged=False,
                error="Perspective API key not configured"
            )
        
        try:
            requested_attributes = {}
            for attr in self._attributes:
                requested_attributes[attr] = {}
            
            payload = {
                "comment": {"text": content},
                "languages": [self._language],
                "requestedAttributes": requested_attributes,
            }
            
            url = f"{self.API_URL}?key={self._api_key}"
            data = json.dumps(payload).encode("utf-8")
            
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            
            categories = {}
            scores = {}
            flagged = False
            
            attribute_scores = response_data.get("attributeScores", {})
            
            for attr, data in attribute_scores.items():
                score = data.get("summaryScore", {}).get("value", 0)
                scores[attr.lower()] = score
                
                is_flagged = score >= self._threshold
                categories[attr.lower()] = is_flagged
                
                if is_flagged:
                    flagged = True
            
            return AICheckResult(
                flagged=flagged,
                categories=categories,
                scores=scores,
                raw_response=response_data
            )
            
        except urllib.error.HTTPError as e:
            logger.error(f"Perspective API HTTP error: {e.code} - {e.reason}")
            raise AIBackendError(
                f"Perspective API error: {e.reason}",
                backend="perspective",
                status_code=e.code
            )
        except urllib.error.URLError as e:
            logger.error(f"Perspective API connection error: {e.reason}")
            raise AIBackendError(
                f"Perspective API connection error: {e.reason}",
                backend="perspective"
            )
        except json.JSONDecodeError as e:
            logger.error(f"Perspective API response parse error: {e}")
            raise AIBackendError(
                "Failed to parse Perspective response",
                backend="perspective"
            )
        except Exception as e:
            logger.error(f"Perspective moderation check failed: {e}")
            return AICheckResult(
                flagged=False,
                error=str(e)
            )
    
    @classmethod
    def get_backend_name(cls) -> str:
        return "perspective"
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> list:
        """Validate Perspective adapter configuration."""
        issues = []
        
        api_key = config.get("perspective_api_key")
        if not api_key:
            issues.append("perspective_api_key is required")
        elif not isinstance(api_key, str):
            issues.append("perspective_api_key must be a string")
        
        threshold = config.get("perspective_threshold")
        if threshold is not None:
            if not isinstance(threshold, (int, float)):
                issues.append("perspective_threshold must be a number")
            elif threshold < 0 or threshold > 1:
                issues.append("perspective_threshold must be between 0 and 1")
        
        attributes = config.get("perspective_attributes")
        if attributes is not None:
            if not isinstance(attributes, list):
                issues.append("perspective_attributes must be a list")
            else:
                valid_attrs = {
                    "TOXICITY", "SEVERE_TOXICITY", "IDENTITY_ATTACK",
                    "INSULT", "PROFANITY", "THREAT", "SEXUALLY_EXPLICIT",
                    "FLIRTATION", "SPAM", "INCOHERENT", "INFLAMMATORY",
                }
                for attr in attributes:
                    if attr not in valid_attrs:
                        issues.append(f"Unknown Perspective attribute: {attr}")
        
        return issues
