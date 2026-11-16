"""The transcription API endpoint must be an absolute HTTPS URL."""

import pytest

from src.core.artifacts.transcription.provider import OpenAIWhisperProvider
from src.core.security.hardening import HardeningViolation


def test_default_endpoint_is_accepted():
    provider = OpenAIWhisperProvider({"openai_api_key": "k"})
    assert provider._api_url.startswith("https://")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1/audio",
        "ftp://x/y",
        "//host/path",
        "https://u:p@host/x",
    ],
)
def test_unsafe_endpoints_are_rejected(url):
    with pytest.raises(HardeningViolation):
        OpenAIWhisperProvider({"openai_api_key": "k", "openai_api_base": url})
