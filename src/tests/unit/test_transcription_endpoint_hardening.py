"""Regression tests for transcription provider endpoint hardening.

Two independent failures are covered here:

* ``openai_api_base`` used to be checked only for its URL *shape*. An HTTPS URL
  pointing at loopback, a private network or the cloud instance-metadata
  address passed the gate and the provider then POSTed its Bearer token there.
* ``azure_region`` used to be interpolated verbatim into a request hostname,
  so a value containing ``@``/``#``/``/`` could redirect the subscription-key
  request to an attacker-controlled host.
"""

import pytest

from src.core.artifacts.transcription.provider import (
    AzureSpeechProvider,
    OpenAIWhisperProvider,
    _validate_azure_region,
)
from src.core.security.hardening import HardeningViolation


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/v1/audio/transcriptions",
        "https://127.0.0.1:8443/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/internal",
        "https://192.168.1.10/v1",
        "https://[::1]/v1",
        "https://[fd00::1]/v1",
        "https://localhost/v1",
        "https://api.internal/v1",
        "https://relay.local/v1",
        "https://127.0.0.1./v1",
        "https://0x7f000001/v1",
        "https://2130706433/v1",
        "https://0177.0.0.1/v1",
    ],
)
def test_openai_endpoint_rejects_local_and_private_destinations(url):
    with pytest.raises(HardeningViolation):
        OpenAIWhisperProvider({"openai_api_key": "k", "openai_api_base": url})


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1/audio/transcriptions",
        "https://openai.example.test/v1",
        "https://8.8.8.8/v1",
    ],
)
def test_openai_endpoint_accepts_public_destinations(url):
    provider = OpenAIWhisperProvider({"openai_api_key": "k", "openai_api_base": url})
    assert provider._api_url == url


@pytest.mark.parametrize(
    "region",
    [
        "eastus@evil.test#",
        "eastus/../..@evil.test",
        "evil.test/api",
        "eastus?x=1",
        "eastus path",
        "east_us",
        "-eastus",
        "eastus-",
        "",
        None,
    ],
)
def test_azure_region_rejects_host_injection(region):
    with pytest.raises(ValueError):
        AzureSpeechProvider({"azure_key": "k", "azure_region": region})


@pytest.mark.parametrize("region", ["eastus", "westus2", "WestEurope"])
def test_azure_region_accepts_plain_labels(region):
    expected = _validate_azure_region(region)
    provider = AzureSpeechProvider({"azure_key": "k", "azure_region": region})
    assert provider._region == expected
    assert "@" not in provider._region
    assert "/" not in provider._region
