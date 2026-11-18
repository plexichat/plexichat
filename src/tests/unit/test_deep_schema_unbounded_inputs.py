"""Regression tests for request-schema fields that escaped the input bounds.

The HTTP body cap (10 MB) is not a field bound. A JSON body can stay under the
cap while still carrying a value large enough to make a synchronous password
verification expensive, or a display string large enough to be persisted and
echoed to every client. These tests pin the two boundaries that were missing.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.artifacts import ConvertUploadRequest
from src.api.schemas.auth import MAX_PASSWORD_INPUT_LENGTH
from src.api.schemas.dsar import DSARRequestBody


def test_dsar_password_rejects_oversized_input():
    """The DSAR verification password shares the auth password ceiling."""
    model = DSARRequestBody(password="x" * MAX_PASSWORD_INPUT_LENGTH)
    assert len(model.password) == MAX_PASSWORD_INPUT_LENGTH

    with pytest.raises(ValidationError):
        DSARRequestBody(password="x" * (MAX_PASSWORD_INPUT_LENGTH + 1))


def test_convert_upload_title_rejects_oversized_input():
    """The convert-upload title uses the same ceiling as the create/patch titles."""
    model = ConvertUploadRequest(attachment_id="123", title="x" * 500)
    assert len(model.title) == 500

    with pytest.raises(ValidationError):
        ConvertUploadRequest(attachment_id="123", title="x" * 501)


def test_convert_upload_title_rejects_empty_input():
    """An empty title override is rejected just like the create/patch title."""
    with pytest.raises(ValidationError):
        ConvertUploadRequest(attachment_id="123", title="")
