"""
Tests for the Shieldstral local AI adapter.

Uses mocked HTTP responses to test the adapter without a real llama-server.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from src.core.automod.ai.shieldstral import ShieldstralAdapter
from src.core.automod.models import AIBackendType
from src.core.automod.exceptions import (
    AIBackendError,
    AIBackendTimeoutError,
    AIBackendUnavailableError,
)


def _mock_response(payload: dict):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(payload).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def _logprobs_payload(yes_lp: float, no_lp: float, answer: str = "yes") -> dict:
    """Build a chat-completions response with first-token log-probs."""
    return {
        "model": "shieldstral",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "logprobs": {
                    "content": [
                        {
                            "token": answer,
                            "logprob": yes_lp,
                            "top_logprobs": [
                                {
                                    "id": 0,
                                    "token": "yes",
                                    "bytes": [121, 101, 115],
                                    "logprob": yes_lp,
                                },
                                {
                                    "id": 1,
                                    "token": "no",
                                    "bytes": [110, 111],
                                    "logprob": no_lp,
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    }


@pytest.mark.automod
class TestShieldstralAdapter:
    """Tests for ShieldstralAdapter."""

    def test_is_available_with_endpoint(self):
        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        assert adapter.is_available()

    def test_is_not_available_without_endpoint(self):
        adapter = ShieldstralAdapter({})
        assert not adapter.is_available()

    def test_raises_unavailable_without_endpoint(self):
        adapter = ShieldstralAdapter({})

        with pytest.raises(AIBackendUnavailableError):
            adapter.check_content("test content")

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_successful_check_flagged(self, mock_urlopen):
        """A strong 'yes' probability should flag the message."""
        mock_urlopen.return_value = _mock_response(_logprobs_payload(-0.1, -2.5))

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("bad content")

        assert result.flagged
        assert result.scores["safety"] > 0.8
        assert result.categories["safety"] is True
        assert result.backend == AIBackendType.SHIELDSTRAL

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_successful_check_safe(self, mock_urlopen):
        """A strong 'no' probability should not flag the message."""
        mock_urlopen.return_value = _mock_response(_logprobs_payload(-3.2, -0.05, "no"))

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("normal content")

        assert not result.flagged
        assert result.scores["safety"] < 0.2

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_threshold_behavior(self, mock_urlopen):
        """A borderline score below the threshold should not flag."""
        # yes=-1.5, no=-1.6 -> score ~0.52
        mock_urlopen.return_value = _mock_response(_logprobs_payload(-1.5, -1.6))
        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        assert adapter.check_content("x").flagged

        # Higher threshold flips the same response to safe.
        adapter = ShieldstralAdapter(
            {"endpoint_url": "http://shieldstral:8080", "threshold": 0.6}
        )
        assert not adapter.check_content("x").flagged

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_fallback_to_text_answer(self, mock_urlopen):
        """Without log-probs, the plain yes/no answer is used."""
        payload = {
            "model": "shieldstral",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "yes"}}
            ],
        }
        mock_urlopen.return_value = _mock_response(payload)

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("x")

        assert result.flagged
        assert result.scores["safety"] == 1.0

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_unparseable_answer_scores_safe(self, mock_urlopen):
        """An unparseable answer degrades to safe (no false positives)."""
        payload = {
            "model": "shieldstral",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "maybe"}}
            ],
        }
        mock_urlopen.return_value = _mock_response(payload)

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("x")

        assert not result.flagged
        assert result.scores["safety"] == 0.0

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_request_includes_logprobs(self, mock_urlopen):
        """The chat request should ask for log-probs so scores are calibrated."""
        mock_urlopen.return_value = _mock_response(_logprobs_payload(-0.1, -2.5))

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        adapter.check_content("x")

        request = mock_urlopen.call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        assert body["logprobs"] is True
        assert body["top_logprobs"] == 2
        assert body["max_tokens"] == 8
        assert body["temperature"] == 0.0
        assert (
            "policy" in body["messages"][0]["content"].lower()
            or "harmful" in body["messages"][0]["content"].lower()
        )

    def test_get_categories(self):
        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        assert adapter.get_categories()["safety"]

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_token_markers_normalized(self, mock_urlopen):
        """GPT-2/SentencePiece space markers ('Ġ', '▁') must not break matching."""
        payload = _logprobs_payload(-0.1, -2.5, answer="yes")
        payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"] = [
            {"id": 0, "token": "\u0120yes", "bytes": None, "logprob": -0.1},
            {"id": 1, "token": "\u2581no", "bytes": None, "logprob": -2.5},
        ]
        mock_urlopen.return_value = _mock_response(payload)

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("bad content")

        assert result.flagged
        assert result.scores["safety"] > 0.8

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_text_fallback_with_marker(self, mock_urlopen):
        """Text fallback should also normalize marker-prefixed answers."""
        payload = {
            "model": "shieldstral",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "\u0120yes"}}
            ],
        }
        mock_urlopen.return_value = _mock_response(payload)

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("x")

        assert result.flagged
        assert result.scores["safety"] == 1.0

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_only_yes_token_scores_max(self, mock_urlopen):
        """Only a 'yes' token present -> certainty it is unsafe."""
        payload = _logprobs_payload(-0.1, -2.5)
        payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"] = [
            {"id": 0, "token": "yes", "bytes": None, "logprob": -0.1}
        ]
        mock_urlopen.return_value = _mock_response(payload)

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("x")

        assert result.flagged
        assert result.scores["safety"] == 1.0

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_only_no_token_scores_zero(self, mock_urlopen):
        """Only a 'no' token present -> certainty it is safe."""
        payload = _logprobs_payload(-0.1, -2.5)
        payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"] = [
            {"id": 0, "token": "no", "bytes": None, "logprob": -0.1}
        ]
        mock_urlopen.return_value = _mock_response(payload)

        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("x")

        assert not result.flagged
        assert result.scores["safety"] == 0.0

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_missing_content_falls_back_safe(self, mock_urlopen):
        """Missing log-probs and empty content degrades to safe."""
        mock_urlopen.return_value = _mock_response(
            {"model": "shieldstral", "choices": []}
        )
        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})
        result = adapter.check_content("x")

        assert not result.flagged
        assert result.scores["safety"] == 0.0

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_http_error_raises_backend_error(self, mock_urlopen):
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "http://shieldstral:8080", 503, "Unavailable", None, None
        )
        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})

        with pytest.raises(AIBackendError):
            adapter.check_content("x")

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_read_timeout_raises_timeout_error(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("timed out")
        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})

        with pytest.raises(AIBackendTimeoutError):
            adapter.check_content("x")

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_connection_error_raises_backend_error(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")
        adapter = ShieldstralAdapter({"endpoint_url": "http://shieldstral:8080"})

        with pytest.raises(AIBackendError):
            adapter.check_content("x")

    @patch("src.core.automod.ai.shieldstral.urlopen")
    def test_invalid_endpoint_url_raises(self, mock_urlopen):
        adapter = ShieldstralAdapter({"endpoint_url": "not-a-url"})

        with pytest.raises(AIBackendError):
            adapter.check_content("x")
