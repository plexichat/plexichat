"""Regression: export IDs must pass the same ASCII-only snowflake gate as
every other ID boundary.

``validate_export_id`` used a bare ``int()`` which accepts Unicode decimal
homoglyphs and PEP 515 separators that the request path rejects. That is a
parser differential: the ID this helper normalises for storage object names
can disagree with the raw token a caller carries into a later lookup.
"""

import pytest

from src.core.dsar.security import (
    InvalidExportInput,
    safe_export_filename,
    validate_export_id,
)


def test_canonical_ids_still_accepted():
    assert validate_export_id("123") == 123
    assert validate_export_id(456) == 456
    assert safe_export_filename(123, "json") == "dsar_123.json"


@pytest.mark.parametrize(
    "homoglyph",
    [
        "\u0661\u0662\u0663",  # Arabic-Indic 123
        "\uff11\uff12\uff13",  # fullwidth 123
        "1_2_3",  # PEP 515 separators
        "\u06f1\u06f2\u06f3",  # Extended Arabic-Indic 123
    ],
)
def test_non_ascii_or_separator_ids_rejected(homoglyph):
    with pytest.raises(InvalidExportInput):
        validate_export_id(homoglyph)
    with pytest.raises(InvalidExportInput):
        safe_export_filename(homoglyph, "json")


def test_out_of_range_and_bool_still_rejected():
    for bad in (0, -1, 2**63, None, True, 1.5):
        with pytest.raises(InvalidExportInput):
            validate_export_id(bad)
