"""
Tests for Perspective API adapter.

Uses mocked HTTP responses to test the adapter without real API calls.
"""

import pytest
import json
from unittest.mock import patch, MagicMock

from src.core.automod.ai.perspective import PerspectiveAdapter
from src.core.automod.models import AIBackendType
from src.core.automod.exceptions import AIBackendUnavailableError


@pytest.mark.automod
class TestPerspectiveAdapter:
    """Tests for PerspectiveAdapter."""

    def test_is_available_with_key(self):
        """Test adapter is available when API key is set."""
        adapter = PerspectiveAdapter({"api_key": "test-key"})
        assert adapter.is_available()

    def test_is_not_available_without_key(self):
        """Test adapter is not available without API key."""
        adapter = PerspectiveAdapter({})
        assert not adapter.is_available()

    def test_raises_unavailable_without_key(self):
        """Test check_content raises when no API key."""
        adapter = PerspectiveAdapter({})

        with pytest.raises(AIBackendUnavailableError):
            adapter.check_content("test content")

    @patch("src.core.automod.ai.perspective.urlopen")
    def test_successful_toxicity_check(self, mock_urlopen):
        """Test successful Perspective API call."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "attributeScores": {
                    "TOXICITY": {"summaryScore": {"value": 0.9}},
                    "SEVERE_TOXICITY": {"summaryScore": {"value": 0.3}},
                }
            }
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = PerspectiveAdapter({"api_key": "test-key", "threshold": 0.7})
        result = adapter.check_content("toxic content")

        assert result.flagged
        assert result.backend == AIBackendType.PERSPECTIVE
        assert result.scores["TOXICITY"] == 0.9
        assert result.categories["TOXICITY"] is True

    @patch("src.core.automod.ai.perspective.urlopen")
    def test_clean_content_not_flagged(self, mock_urlopen):
        """Test clean content is not flagged."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "attributeScores": {
                    "TOXICITY": {"summaryScore": {"value": 0.1}},
                    "PROFANITY": {"summaryScore": {"value": 0.05}},
                }
            }
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = PerspectiveAdapter({"api_key": "test-key", "threshold": 0.7})
        result = adapter.check_content("hello friend")

        assert not result.flagged

    @patch("src.core.automod.ai.perspective.urlopen")
    def test_custom_attributes(self, mock_urlopen):
        """Test custom attribute selection."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"attributeScores": {"INSULT": {"summaryScore": {"value": 0.8}}}}
        ).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = PerspectiveAdapter(
            {"api_key": "test-key", "attributes": ["INSULT"], "threshold": 0.7}
        )
        result = adapter.check_content("insulting content")

        assert result.flagged
        assert "INSULT" in result.scores

    def test_get_categories(self):
        """Test getting supported categories."""
        adapter = PerspectiveAdapter(
            {"api_key": "test-key", "attributes": ["TOXICITY", "PROFANITY"]}
        )
        categories = adapter.get_categories()

        assert "TOXICITY" in categories
        assert "PROFANITY" in categories
        assert "INSULT" not in categories
