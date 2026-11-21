"""Regression: AI adapter threshold/timeout config must be bounded.

The admin AutoMod config stores ``threshold`` and ``timeout_seconds`` as raw
JSON numbers. All four adapters used them directly in ``score >= threshold``
and ``urlopen(timeout=...)``:

* a NaN/inf threshold made the comparison always False, silently disabling
  score-based moderation (fail open);
* a negative/zero threshold made every score match, flooding ordinary
  messages with false violations and moderation actions;
* an absurd timeout pinned a worker thread per moderated message (or
  overflowed ``socket.settimeout``), stalling/breaking the check pipeline.

The bounds live on BaseAIAdapter as data descriptors so every backend is
protected regardless of which write path supplied the config.
"""

import math

import pytest

from src.core.automod.ai.base import (
    DEFAULT_THRESHOLD,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
)
from src.core.automod.ai.custom import CustomAdapter
from src.core.automod.ai.openai import OpenAIAdapter
from src.core.automod.ai.shieldstral import ShieldstralAdapter
from src.core.automod.ai.perspective import PerspectiveAdapter


ADAPTERS = [CustomAdapter, OpenAIAdapter, ShieldstralAdapter, PerspectiveAdapter]

BAD_THRESHOLDS = [
    float("nan"),
    float("inf"),
    float("-inf"),
    -1.0,
    0.0,
    10**9,
    True,
    "0.5",
]


@pytest.mark.parametrize("adapter_cls", ADAPTERS)
@pytest.mark.parametrize("raw", BAD_THRESHOLDS)
def test_invalid_threshold_falls_back_to_safe_default(adapter_cls, raw):
    adapter = adapter_cls({"threshold": raw})
    assert adapter._threshold == DEFAULT_THRESHOLD
    assert math.isfinite(adapter._threshold)


@pytest.mark.parametrize("adapter_cls", ADAPTERS)
def test_legitimate_threshold_preserved(adapter_cls):
    adapter = adapter_cls({"threshold": 0.7})
    assert adapter._threshold == 0.7


@pytest.mark.parametrize(
    "raw,expected",
    [
        (float("nan"), DEFAULT_TIMEOUT_SECONDS),
        (float("inf"), DEFAULT_TIMEOUT_SECONDS),
        (-1, DEFAULT_TIMEOUT_SECONDS),
        (0, DEFAULT_TIMEOUT_SECONDS),
        (10**12, DEFAULT_TIMEOUT_SECONDS),
        (True, DEFAULT_TIMEOUT_SECONDS),
        (30, 30.0),
    ],
)
def test_timeout_bounded(raw, expected):
    adapter = CustomAdapter({"timeout_seconds": raw})
    assert adapter._timeout == expected
    assert 0 < adapter._timeout <= MAX_TIMEOUT_SECONDS


def test_nan_threshold_no_longer_disables_score_flagging():
    """With a NaN threshold the custom backend used to never flag on score."""
    adapter = CustomAdapter({"threshold": float("nan")})
    result = adapter._parse_response({"flagged": False, "scores": {"toxicity": 0.95}})
    assert result.flagged is True


def test_negative_threshold_no_longer_flags_every_score():
    adapter = CustomAdapter({"threshold": -1.0})
    result = adapter._parse_response({"flagged": False, "scores": {"toxicity": 0.0}})
    assert result.flagged is False
