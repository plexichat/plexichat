"""
Tests for custom AI endpoint adapter.

Uses mocked HTTP responses to test the adapter without real API calls.
"""

import pytest
import json
from unittest.mock import patch, MagicMock

from src.core.automod.ai.custom import CustomAdapter
from src.core.automod.models import AIBackendType
from src.core.automod.exceptions import AIBackendUnavailableError


@pytest.mark.automod
class TestCustomAdapter:
    """Tests for CustomAdapter."""

    def test_is_available_with_endpoint(self):
        """Test adapter is available when endpoint is set."""
        adapter = CustomAdapter({"endpoint_url": "https://api.example.com/moderate"})
        assert adapter.is_available()

    def test_is_not_available_without_endpoint(self):
        """Test adapter is not available without endpoint."""
        adapter = CustomAdapter({})
        assert not adapter.is_available()

    def test_raises_unavailable_without_endpoint(self):
        """Test check_content raises when no endpoint."""
        adapter = CustomAdapter({})

        with pytest.raises(AIBackendUnavailableError):
            adapter.check_content("test content")

    @patch("src.core.automod.ai.custom.urlopen")
    def test_successful_check_default_format(self, mock_urlopen):
        """Test successful check with default format."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "flagged": True,
                "categories": {"spam": True, "toxic": False},
                "scores": {"spam": 0.9, "toxic": 0.2},
            }
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = CustomAdapter({"endpoint_url": "https://api.example.com/moderate"})
        result = adapter.check_content("spam content")

        assert result.flagged
        assert result.backend == AIBackendType.CUSTOM
        assert result.categories["spam"] is True

    @patch("src.core.automod.ai.custom.urlopen")
    def test_openai_format(self, mock_urlopen):
        """Test OpenAI-compatible format."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"flagged": False, "score": 0.3}
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = CustomAdapter(
            {
                "endpoint_url": "https://api.example.com/moderate",
                "request_format": "openai",
            }
        )
        result = adapter.check_content("test")

        assert not result.flagged

    @patch("src.core.automod.ai.custom.urlopen")
    def test_threshold_based_flagging(self, mock_urlopen):
        """Test threshold-based flagging."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"score": 0.8}).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = CustomAdapter(
            {"endpoint_url": "https://api.example.com/moderate", "threshold": 0.5}
        )
        result = adapter.check_content("test")

        assert result.flagged

    @patch("src.core.automod.ai.custom.urlopen")
    def test_custom_headers(self, mock_urlopen):
        """Test custom headers are sent."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"flagged": False}).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = CustomAdapter(
            {
                "endpoint_url": "https://api.example.com/moderate",
                "api_key": "secret-key",
                "auth_header": "X-API-Key",
                "auth_prefix": "",
                "headers": {"X-Custom": "value"},
            }
        )
        adapter.check_content("test")

        call_args = mock_urlopen.call_args[0][0]
        assert call_args.headers["X-api-key"] == "secret-key"
        assert call_args.headers["X-custom"] == "value"
