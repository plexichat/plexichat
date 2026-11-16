"""Report route fields are bounded before they reach the report store."""

import pytest
from fastapi import HTTPException

from src.api.routes import reports as routes


def test_reason_and_details_length_caps():
    assert routes._bounded_report_field("x" * 500, "reason", 500) == "x" * 500
    with pytest.raises(HTTPException) as exc:
        routes._bounded_report_field("x" * 501, "reason", 500)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        routes._bounded_report_field("x" * 2001, "details", 2000)


def test_required_field_and_control_characters():
    with pytest.raises(HTTPException):
        routes._bounded_report_field(None, "reason", 500, required=True)
    with pytest.raises(HTTPException):
        routes._bounded_report_field("hello\nworld", "reason", 500)
    assert routes._bounded_report_field(None, "details", 2000) is None


def test_message_content_is_truncated():
    result = routes._bounded_report_field(
        "x" * 5000, "message_content", 500, truncate=True
    )
    assert len(result) == routes.MAX_REPORT_MESSAGE_CONTENT


def test_evidence_list_cap():
    ok = [str(i) for i in range(routes.MAX_EVIDENCE_IDS)]
    assert routes._bounded_evidence_ids(ok) == ok
    with pytest.raises(HTTPException):
        routes._bounded_evidence_ids(ok + ["9999"])
    with pytest.raises(HTTPException):
        routes._bounded_evidence_ids(["x" * (routes.MAX_EVIDENCE_ID_LENGTH + 1)])
    with pytest.raises(HTTPException):
        routes._bounded_evidence_ids("not-a-list")


def test_pagination_params_are_clamped():
    assert routes._bounded_page_params(10, 3) == (10, 3)
    assert routes._bounded_page_params(-1, -5) == (0, 0)
    assert routes._bounded_page_params(10**9, 10**9) == (
        routes.MAX_REPORT_PAGE_SIZE,
        10**9,
    )
    assert routes._bounded_page_params("abc", "def") == (0, 0)
