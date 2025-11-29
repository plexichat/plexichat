"""
Tests for OpenAI moderation adapter.

Uses mocked HTTP responses to test the adapter without real API calls.
"""

import pytest
import json
from unittest.mock import patch, MagicMock

from src.core.automod.ai.openai import OpenAIAdapter
from src.core.automod.models import AIBackendType
from src.core.automod.exceptions import (
    AIBackendError,
    AIBackendUnavailableError,
    AIBackendTimeoutError,
)


@pytest.mark.automod
class TestOpenAIAdapter:
    """Tests for OpenAIAdapter."""
    
    def test_is_available_with_key(self):
        """Test adapter is available when API key is set."""
        adapter = OpenAIAdapter({"api_key": "sk-test-key"})
        assert adapter.is_available()
    
    def test_is_not_available_without_key(self):
        """Test adapter is not available without API key."""
        adapter = OpenAIAdapter({})
        assert not adapter.is_available()
    
    def test_raises_unavailable_without_key(self):
        """Test check_content raises when no API key."""
        adapter = OpenAIAdapter({})
        
        with pytest.raises(AIBackendUnavailableError):
            adapter.check_content("test content")
    
    @patch("src.core.automod.ai.openai.urlopen")
    def test_successful_moderation_check(self, mock_urlopen):
        """Test successful moderation API call."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "results": [{
                "flagged": True,
                "categories": {
                    "hate": False,
                    "violence": True
                },
                "category_scores": {
                    "hate": 0.1,
                    "violence": 0.8
                }
            }]
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        adapter = OpenAIAdapter({"api_key": "sk-test-key"})
        result = adapter.check_content("violent content")
        
        assert result.flagged
        assert result.backend == AIBackendType.OPENAI
        assert result.categories["violence"] is True
        assert result.scores["violence"] == 0.8
    
    @patch("src.core.automod.ai.openai.urlopen")
    def test_clean_content_not_flagged(self, mock_urlopen):
        """Test clean content is not flagged."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "results": [{
                "flagged": False,
                "categories": {
                    "hate": False,
                    "violence": False
                },
                "category_scores": {
                    "hate": 0.01,
                    "violence": 0.02
                }
            }]
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        adapter = OpenAIAdapter({"api_key": "sk-test-key"})
        result = adapter.check_content("hello world")
        
        assert not result.flagged
    
    @patch("src.core.automod.ai.openai.urlopen")
    def test_threshold_based_flagging(self, mock_urlopen):
        """Test content flagged based on threshold."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "results": [{
                "flagged": False,
                "categories": {"hate": False},
                "category_scores": {"hate": 0.6}
            }]
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        adapter = OpenAIAdapter({"api_key": "sk-test-key", "threshold": 0.5})
        result = adapter.check_content("borderline content")
        
        assert result.flagged
    
    def test_get_categories(self):
        """Test getting supported categories."""
        adapter = OpenAIAdapter({"api_key": "sk-test-key"})
        categories = adapter.get_categories()
        
        assert "hate" in categories
        assert "violence" in categories
        assert "sexual" in categories
