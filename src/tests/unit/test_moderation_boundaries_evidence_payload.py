"""The combined report/evidence payload has a hard byte ceiling."""

import inspect
import pytest
from fastapi import HTTPException

from src.api.routes import reports as routes


def test_small_payload_accepted():
    assert routes._bounded_report_payload("spam", None, None, [1, 2, 3]) > 0


def test_total_payload_capped():
    big = "x" * routes.MAX_REPORT_PAYLOAD_BYTES
    with pytest.raises(HTTPException) as exc:
        routes._bounded_report_payload(big, big, big, [])
    assert exc.value.status_code == 400


def test_evidence_payload_capped():
    evidence = [str(i) for i in range(routes.MAX_EVIDENCE_IDS)]
    total = routes._bounded_report_payload(None, None, None, evidence)
    assert total < routes.MAX_REPORT_PAYLOAD_BYTES
    with pytest.raises(HTTPException):
        routes._bounded_report_payload(None, None, None, ["1" * 120] * 100)


def test_routes_enforce_payload_bound():
    assert (
        "_bounded_report_payload(reason, details, message_content"
        in inspect.getsource(routes.report_message)
    )
    assert (
        "_bounded_report_payload(reason, details, None, evidence_ids)"
        in inspect.getsource(routes.report_user)
    )
