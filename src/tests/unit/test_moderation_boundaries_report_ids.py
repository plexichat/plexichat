"""Reported user/message IDs must be real snowflakes."""

import inspect
import pytest
from fastapi import HTTPException

from src.api.routes import reports as routes


@pytest.mark.parametrize("value", [1, "123", 2**63 - 1, " 42 "])
def test_valid_ids(value):
    assert routes._parse_report_id(value, "id") == int(value)


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        2**63,
        "0",
        "-5",
        "abc",
        "1e5",
        None,
        "1" * 40,
        "1\n2",
        [],
    ],
)
def test_invalid_ids_rejected(value):
    with pytest.raises(HTTPException) as exc:
        routes._parse_report_id(value, "id")
    assert exc.value.status_code == 400


def test_report_routes_use_the_guard():
    source = inspect.getsource(routes.report_message)
    assert "_parse_report_id(body.message_id" in source
    assert "_parse_report_id(body.channel_id" in source
    source = inspect.getsource(routes.report_user)
    assert "_parse_report_id(body.user_id" in source
    assert "int(body.user_id)" not in source
