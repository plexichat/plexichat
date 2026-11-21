"""Regression: provider transcription requests must not follow redirects.

The provider endpoints are validated once (public HTTPS, no credentials,
backslash-free authority) before a request is sent. ``requests`` follows 3xx
responses by default, however, so a redirect from the endpoint can re-steer the
connection to an address the SSRF gate never approved. On a 307/308 the
multipart body (the uploaded recording) is replayed to the redirect target, and
provider headers such as ``Ocp-Apim-Subscription-Key`` are *not* stripped on a
cross-host redirect by ``requests``. The rest of the codebase refuses redirects
at the transport (``src.utils.outbound.urlopen``); these provider calls do too.
"""

from __future__ import annotations

import pytest

from src.core.artifacts.transcription.provider import (
    AzureSpeechProvider,
    OpenAIWhisperProvider,
)


class _Captured(Exception):
    """Sentinel used to stop the provider call once args are captured."""


def _capture(monkeypatch, module_attr: str, captured: dict):
    def _fake(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        raise _Captured()

    monkeypatch.setattr("requests." + module_attr, _fake)


def test_openai_provider_does_not_follow_redirects(tmp_path, monkeypatch):
    provider = OpenAIWhisperProvider(
        {
            "openai_api_key": "test-key",
            "openai_api_base": "https://api.openai.com/v1/audio/transcriptions",
        }
    )
    recording = tmp_path / "clip.wav"
    recording.write_bytes(b"RIFFfake")

    captured: dict = {}
    _capture(monkeypatch, "post", captured)

    with pytest.raises(_Captured):
        provider._transcribe_blocking(str(recording), {})

    assert captured["args"][0] == provider._api_url
    assert captured["kwargs"]["allow_redirects"] is False
    assert "Bearer test-key" in captured["kwargs"]["headers"]["Authorization"]


def test_azure_create_does_not_follow_redirects(monkeypatch):
    provider = AzureSpeechProvider({"azure_key": "test-key", "azure_region": "eastus"})

    captured: dict = {}
    _capture(monkeypatch, "post", captured)

    with pytest.raises(_Captured):
        provider._transcribe_rest("https://8.8.8.8/recording.wav", {})

    assert captured["kwargs"]["allow_redirects"] is False
    assert captured["kwargs"]["headers"]["Ocp-Apim-Subscription-Key"] == "test-key"


class _CreateResponse:
    status_code = 201

    @staticmethod
    def json():
        return {"self": "https://eastus.api.cognitive.microsoft.com/id/job-123"}


def test_azure_status_poll_does_not_follow_redirects(monkeypatch):
    provider = AzureSpeechProvider({"azure_key": "test-key", "azure_region": "eastus"})

    calls: list = []

    def _fake_post(*args, **kwargs):
        calls.append(("post", kwargs))
        return _CreateResponse()

    def _fake_get(*args, **kwargs):
        calls.append(("get", kwargs))
        raise _Captured()

    monkeypatch.setattr("requests.post", _fake_post)
    monkeypatch.setattr("requests.get", _fake_get)

    with pytest.raises(_Captured):
        provider._transcribe_rest("https://8.8.8.8/recording.wav", {})

    get_kwargs = [kwargs for verb, kwargs in calls if verb == "get"]
    assert len(get_kwargs) == 1
    assert get_kwargs[0]["allow_redirects"] is False
