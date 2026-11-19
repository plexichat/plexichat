"""Application route ids must pass the bounded snowflake gate.

The application-management handlers used bare ``int(application_id)`` on the
URL id. Python's ``int`` accepts negatives, zero, values past the signed 64-bit
snowflake ceiling and spellings such as ``"1_2"`` (-> 12) or Unicode decimal
homoglyphs (``"٤٢"`` -> 42), so the id that reached the ownership-checked
lookup could differ from the one the caller actually wrote. The rest of the
route layer parses path ids through ``parse_id``; these handlers now do too
and reject non-canonical ids with 400 before any service call.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType
from src.core.security.hardening import SNOWFLAKE_MAX


def _token(user_id=100):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=SnowflakeID(str(user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Every one of these is accepted by int() but rejected by the bounded ASCII
# snowflake gate. They previously flowed into the application lookup as a
# different identifier than the caller wrote.
_BAD_IDS = [
    "-1",  # negative
    "0",  # zero
    str(SNOWFLAKE_MAX + 1),  # just past the signed 64-bit ceiling
    str(2**80),  # far past it (must not 500 on conversion)
    "1_2",  # PEP 515 underscore -> int 12
    "٤٢",  # Arabic-Indic digits -> int 42
    "１２",  # fullwidth digits -> int 12
    "1.5",  # fraction int() would truncate/raise
    "0x10",  # hex prefix int() rejects with ValueError (was a 500)
]

_HANDLERS = [
    ("get_application", lambda aid: {"application_id": aid}),
    ("update_application", lambda aid: {"application_id": aid, "body": None}),
    ("delete_application", lambda aid: {"application_id": aid}),
    ("create_bot", lambda aid: {"application_id": aid}),
    ("regenerate_secret", lambda aid: {"application_id": aid}),
]


@pytest.mark.parametrize("name,kwargs", _HANDLERS)
@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_handler_rejects_noncanonical_id_at_boundary(name, kwargs, bad_id):
    from src.api.routes import applications as routes

    with pytest.raises(HTTPException) as exc:
        _call(getattr(routes, name), current_user=_token(), **kwargs(bad_id))
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == 400


def test_canonical_id_is_forwarded_unchanged(monkeypatch):
    """A well-formed snowflake still reaches the core (no happy-path regression)."""
    from src.api.routes import applications as routes

    seen = {}

    def fake_get_application(application_id, user_id):
        seen["id"] = application_id
        return {
            "id": application_id,
            "owner_id": user_id,
            "name": "app",
            "created_at": 0,
        }

    monkeypatch.setattr(routes.applications, "get_application", fake_get_application)
    _call(routes.get_application, application_id="123", current_user=_token())
    assert seen["id"] == 123
