"""Regression: request models reject internally contradictory state.

Field-level bounds cannot see that two fields disagree. An export window with
``to_date`` before ``from_date`` or a workbook range ending before it starts
used to validate cleanly and fail (or silently do nothing) downstream.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.messages import TranscriptExportRequest
from src.api.schemas.sum import (
    SumConditionalFormatRequest,
    SumRangePermissionRequest,
    SumSortRequest,
    SumValidationRequest,
)


def test_transcript_window_rejects_inverted_dates():
    with pytest.raises(ValidationError, match="to_date"):
        TranscriptExportRequest(from_date="2024-06-01", to_date="2024-01-01")  # pyright: ignore[reportCallIssue]


def test_transcript_window_rejects_inverted_datetimes():
    with pytest.raises(ValidationError, match="to_date"):
        TranscriptExportRequest(  # pyright: ignore[reportCallIssue]
            from_date="2024-06-01T12:00:00Z",
            to_date="2024-06-01T11:00:00Z",
        )


def test_transcript_window_allows_ordered_and_partial_bounds():
    TranscriptExportRequest(from_date="2024-01-01", to_date="2024-06-01")  # pyright: ignore[reportCallIssue]
    TranscriptExportRequest(from_date="2024-01-01")  # pyright: ignore[reportCallIssue]
    TranscriptExportRequest(to_date="2024-06-01")  # pyright: ignore[reportCallIssue]
    TranscriptExportRequest()  # pyright: ignore[reportCallIssue]
    # Equal bounds describe a zero-length window and are accepted.
    TranscriptExportRequest(from_date="2024-01-01", to_date="2024-01-01")  # pyright: ignore[reportCallIssue]


def test_sum_sort_rejects_inverted_row_range():
    with pytest.raises(ValidationError, match="end_row"):
        SumSortRequest(column=0, start_row=10, end_row=3, base_revision=0)  # pyright: ignore[reportCallIssue]
    SumSortRequest(column=0, start_row=0, end_row=10, base_revision=0)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    "model,kwargs",
    [
        (SumRangePermissionRequest, {"row_start": 5, "row_end": 2}),
        (SumRangePermissionRequest, {"column_start": 9, "column_end": 3}),
        (SumValidationRequest, {"row_start": 5, "row_end": 2}),
        (SumValidationRequest, {"column_start": 9, "column_end": 3}),
        (SumConditionalFormatRequest, {"row_start": 5, "row_end": 2}),
        (SumConditionalFormatRequest, {"column_start": 9, "column_end": 3}),
    ],
)
def test_sum_range_models_reject_inverted_bounds(model, kwargs):
    base = {"row_start": 0, "row_end": 10, "column_start": 0, "column_end": 10}
    base.update(kwargs)
    with pytest.raises(ValidationError):
        model(**base)
