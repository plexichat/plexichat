"""Regression: policy-annotated text rejects control and bidi-override chars.

The HTML-metacharacter rule used to be the whole story for ``UIText``. A value
could still carry a C1 control or a Unicode bidi override (U+202E and friends),
which reorders how the string renders without changing a single byte of the
stored value -- a stored-display spoof that an output encoder cannot fix.
The check lives in one shared validator that every policy annotation runs.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.policy_types import (
    UIText,
    reject_unsafe_unicode,
)
from src.api.schemas.servers import ChannelCreateRequest
from pydantic import BaseModel, Field


class _TextCarrier(BaseModel):
    value: UIText = Field(..., min_length=1, max_length=200)


@pytest.mark.parametrize(
    "value",
    [
        "general\x00",  # C0 NUL
        "general\x1b[31m",  # C0 ESC (terminal escape)
        "general\x7f",  # DEL
        "general\x81",  # C1 control
        "general\x9f",  # C1 control
        "report\u202efdp.exe",  # RIGHT-TO-LEFT OVERRIDE
        "abc\u202a\u202cdef",  # LRE + PDF embedding
        "abc\u2066\u2069def",  # LEFT-TO-RIGHT ISOLATE + PDI
        "a\u200eb",  # LEFT-TO-RIGHT MARK
        "a\u061cb",  # ARABIC LETTER MARK
        "a\u2060b",  # WORD JOINER
        "a\ufeffb",  # BYTE ORDER MARK
        "a\u2028b",  # LINE SEPARATOR
    ],
)
def test_shared_validator_rejects_unsafe_unicode(value):
    with pytest.raises(ValueError):
        reject_unsafe_unicode(value, "value")


@pytest.mark.parametrize(
    "value",
    [
        "café",
        "日本語",
        "emoji 👨‍👩‍👧 family",  # ZWJ is legitimate and stays allowed
        "line one — line two",
    ],
)
def test_shared_validator_allows_ordinary_unicode(value):
    assert reject_unsafe_unicode(value, "value") == value


def test_policy_annotation_applies_the_shared_validator():
    with pytest.raises(ValidationError):
        _TextCarrier(value="ok\u202evil")


def test_multiple_policy_types_share_the_rule():
    from src.api.schemas.policy_types import UIText200, UIText500

    for annotation in (UIText, UIText200, UIText500):

        class Carrier(BaseModel):
            value: annotation = Field(..., min_length=1)  # pyright: ignore[reportInvalidTypeForm]

        with pytest.raises(ValidationError):
            Carrier(value="ok\u2069bad")


def test_request_model_field_inherits_the_rule():
    # A real request model, not just a synthetic carrier: the policy is applied
    # at the HTTP boundary because the annotation is on the field.
    with pytest.raises(ValidationError):
        ChannelCreateRequest(name="general\u202eexe")  # pyright: ignore[reportCallIssue]
