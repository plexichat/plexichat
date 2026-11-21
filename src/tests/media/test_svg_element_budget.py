"""Regression: SVG sanitation must bound the number of XML elements.

defusedxml stops entity expansion (billion-laughs / XXE) but has no element
budget. A bounded upload made of many tiny, individually allow-listed elements
(<g/>) is expanded into a huge ElementTree, pinning a worker's CPU and RAM for
minutes before any allow-list decision is reached. The sanitizer must reject
such payloads fail-closed, both before parsing and during the tree walk.
"""

import pytest

from src.core.media.security.validation import (
    MAX_SVG_ELEMENTS,
    FileValidator,
    validate_file,
)


def _many_elements_svg(count: int) -> bytes:
    return (
        "<svg xmlns='http://www.w3.org/2000/svg'>" + "<g/>" * count + "</svg>"
    ).encode()


def test_huge_element_svg_rejected_before_parse() -> None:
    # ~200 KB, well under the 4 MB decode cap, but 20x the element budget.
    # Before the fix this parsed into 200k Element nodes (~20 s, tens of MiB).
    payload = _many_elements_svg(MAX_SVG_ELEMENTS * 20)
    ok, reason = FileValidator._sanitize_svg(payload)
    assert ok is False
    assert reason == "too_many_elements"


def test_element_budget_enforced_during_tree_walk() -> None:
    # One element over the cap stays UNDER the lexical '<' pre-check
    # (~MAX+3 '<' vs the 2*MAX threshold), so only the authoritative walk
    # counter can reject it.
    payload = _many_elements_svg(MAX_SVG_ELEMENTS + 1)
    ok, reason = FileValidator._sanitize_svg(payload)
    assert ok is False
    assert reason == "too_many_elements"


def test_validate_file_fails_closed_on_element_bomb() -> None:
    ok, error, _ = validate_file(
        _many_elements_svg(MAX_SVG_ELEMENTS * 20), "bomb.svg", "image/svg+xml"
    )
    assert ok is False
    assert error


@pytest.mark.parametrize("count", [1, 8, 250])
def test_normal_svg_within_element_budget_still_passes(count: int) -> None:
    ok, reason = FileValidator._sanitize_svg(_many_elements_svg(count))
    assert ok is True
    assert reason == ""
