"""Perf + correctness for the printable fast path in reject_control_characters.

``reject_control_characters`` sits on a genuine hot path: header validation,
URL helpers, audit/log fields, filenames and many request/route validators all
call it. Its regex is a character-class ``search`` over the *whole* value, so
every ordinary printable header/log/url pays a full regex-engine scan even
though the value cannot possibly contain a control character.

Every codepoint the regex matches is non-printable, so a fully printable value
cannot match it. ``str.isprintable()`` is a C-level scan and short-circuits the
regex for the overwhelmingly common case. These tests pin both the measurable
cost (the timing test fails on the unoptimized implementation) and the exact
behavioral equivalence (no valid value newly rejected, controls still refused).
"""

from __future__ import annotations

import time

import pytest

from src.core.security.hardening.errors import HardeningViolation
from src.core.security.hardening.text import (
    CONTROL_CHARACTERS,
    reject_control_characters,
)


def _elapsed(fn, iterations: int) -> float:
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    return time.perf_counter() - start


def _median(values) -> float:
    values = sorted(values)
    return values[len(values) // 2]


def test_printable_fastpath_skips_regex_scan():
    # A long, entirely printable value: the common case for headers, log
    # fields and URLs. The regex must scan the whole buffer today.
    text = "A" * 4096
    iterations = 2500

    # Warm both code paths so import/first-call costs do not skew the sample.
    CONTROL_CHARACTERS.search(text)
    reject_control_characters(text)

    def always_regex():
        # The work reject_control_characters performs with no fast path.
        assert not CONTROL_CHARACTERS.search(str(text or ""))

    def optimized():
        reject_control_characters(text)

    baseline = _median([_elapsed(always_regex, iterations) for _ in range(3)])
    actual = _median([_elapsed(optimized, iterations) for _ in range(3)])

    # Without the fast path both functions do the same regex scan -> ratio ~1.
    # With it, the C-level isprintable() scan is several times faster.
    assert baseline / actual > 2.0, (
        f"no measurable fast path: regex={baseline:.4f}s fn={actual:.4f}s"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "plain ascii",
        "header-value.123_ABC",
        "café naïve — ünïcøde",  # printable non-ASCII must still pass
        "emoji 🚀🎉 ok",
        "tab\tis rejected",  # C0 control
        "line\nbreak",  # LF
        "carriage\rreturn",  # CR
        "nul\x00byte",  # NUL
        "del\x7fchar",  # DEL
        "nel\x85char",  # C1 / NEL
        "line\u2028sep",  # Unicode LINE SEPARATOR
        "rtl\u202eoverride",  # bidi override
        "bom\ufeffchar",  # BOM
    ],
)
def test_printable_fastpath_preserves_exact_behavior(value):
    def reference(v):
        text = str(v or "")
        if CONTROL_CHARACTERS.search(text):
            raise HardeningViolation("contains control characters")
        return text

    try:
        expected = reference(value)
    except HardeningViolation:
        expected = "reject"

    try:
        actual = reject_control_characters(value)
    except HardeningViolation:
        actual = "reject"

    assert actual == expected
