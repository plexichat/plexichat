"""Regression: the Azure region may not redirect the provider request host.

``AzureSpeechProvider`` builds its endpoint as
``https://{region}.api.cognitive.microsoft.com/...`` and sends the Azure
subscription key as a header. A region containing ``#``, ``/``, ``@``,
whitespace or control characters changes the request authority (e.g.
``https://attacker.test#.api.cognitive.microsoft.com/``) and exfiltrates the
key. The value is now validated to be a single DNS label up front.
"""

import pytest

from src.core.artifacts.transcription.provider import AzureSpeechProvider


@pytest.mark.parametrize(
    "region",
    [
        "",
        "attacker.test#",
        "attacker.test/",
        "attacker.test?",
        "attacker@host",
        "eastus/../evil",
        "east us",
        "eastus\r\nHost: attacker.test",
        "-eastus",
        "eastus-",
        "a" * 64,
        "../evil",
        "eastus:443",
    ],
)
def test_hostile_azure_region_is_rejected(region):
    with pytest.raises(ValueError):
        AzureSpeechProvider({"azure_key": "key", "azure_region": region})


def test_valid_azure_region_is_normalized():
    provider = AzureSpeechProvider(
        {"azure_key": "key", "azure_region": " WestUS2 "}
    )
    assert provider._region == "westus2"


def test_default_azure_region_is_safe():
    provider = AzureSpeechProvider({"azure_key": "key"})
    assert provider._region == "eastus"
