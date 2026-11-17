"""Security invariants: relationship mutations only accept real snowflake ids.

Data-driven + static suite.  A hostile id must be rejected at the boundary,
before any relationship manager (or the database) sees it.  Both boundary
shapes used by this module are checked:

* ``SnowflakeIDParam`` / ``SnowflakeID`` -- Pydantic types for path/body ids;
* ``parse_id`` -- the core validator called by the mutation route handlers.

No fixtures, database or network access is required.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter

import src.api as api
from src.api.routes.relationships.blocking import BlockingMixin
from src.api.routes.relationships.friend_requests import FriendRequestMixin
from src.api.schemas.common import SnowflakeID, SnowflakeIDParam
from src.core.security.hardening import HardeningViolation, SNOWFLAKE_MAX, parse_id

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.relationships]

ROUTES = (
    Path(__file__).resolve().parents[3] / "src" / "api" / "routes" / "relationships"
)

# Values that must never be accepted as a user id. ``0`` is intentionally
# excluded for the Pydantic types (they allow the documented 0 sentinel) but
# included for ``parse_id`` (which requires a real id >= 1).
BAD_ID_STRINGS = [
    "-1",
    str(SNOWFLAKE_MAX + 1),
    "999999999999999999999999999999",
    "abc",
    "1.5",
    "1e3",
    "0x10",
    "",
    "   ",
    "null",
]

CURRENT_USER = SimpleNamespace(user_id=1)


# ---------------------------------------------------------------------------
# boundary types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", BAD_ID_STRINGS + [True, False, 1.5, [], None])
@pytest.mark.parametrize("schema", [SnowflakeIDParam, SnowflakeID])
def test_snowflake_types_reject_hostile_ids(schema, bad):
    with pytest.raises(ValueError):
        TypeAdapter(schema).validate_python(bad)


@pytest.mark.parametrize("good", [0, 1, 42, SNOWFLAKE_MAX, str(SNOWFLAKE_MAX)])
def test_snowflake_param_accepts_valid_ids(good):
    assert isinstance(TypeAdapter(SnowflakeIDParam).validate_python(good), int)


@pytest.mark.parametrize("good", [0, 1, 42, SNOWFLAKE_MAX, str(SNOWFLAKE_MAX)])
def test_snowflake_response_type_accepts_valid_ids(good):
    assert TypeAdapter(SnowflakeID).validate_python(good) == str(good)


# ---------------------------------------------------------------------------
# core validator used by the route handlers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", BAD_ID_STRINGS + ["0", 0, -5, None, []])
def test_parse_id_rejects_invalid_snowflakes(bad):
    with pytest.raises(HardeningViolation):
        parse_id(bad, "user_id")


@pytest.mark.parametrize("good", ["1", 1, 42, SNOWFLAKE_MAX])
def test_parse_id_accepts_valid_snowflakes(good):
    assert parse_id(good, "user_id") == int(good)


# ---------------------------------------------------------------------------
# route handlers: reject before any domain mutation is attempted
# ---------------------------------------------------------------------------


class _DomainProbe:
    """A truthy stub whose attribute access raises if a mutation slips through."""

    def __getattr__(self, name):
        raise AssertionError(f"domain method {name!r} called with an unvalidated id")


@pytest.mark.parametrize("bad", BAD_ID_STRINGS + ["0", -1, None])
def test_block_route_rejects_bad_id_before_domain_call(monkeypatch, bad):
    monkeypatch.setattr(api, "get_relationships", lambda: _DomainProbe())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            BlockingMixin().block_user(
                SimpleNamespace(user_id=bad), current_user=CURRENT_USER
            )
        )
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad", BAD_ID_STRINGS + ["0", -1, None])
def test_friend_request_route_rejects_bad_id_before_domain_call(monkeypatch, bad):
    monkeypatch.setattr(api, "get_relationships", lambda: _DomainProbe())
    monkeypatch.setattr(api, "get_auth", lambda: _DomainProbe())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            FriendRequestMixin().create_relationship(
                SimpleNamespace(user_id=bad, message=None), current_user=CURRENT_USER
            )
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# static wiring guards: every mutation path declares a snowflake boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["friend_requests.py", "blocking.py"])
def test_body_ids_are_parsed_before_domain_calls(filename):
    source = (ROUTES / filename).read_text(encoding="utf-8")
    assert "from src.core.security.hardening import parse_id" in source
    parse_at = source.index("parse_id(body.user_id")
    # The first domain mutation must appear after the id has been validated.
    domain_calls = [
        source.index(name)
        for name in ("relationships.send_friend_request", "relationships.block_user")
        if name in source
    ]
    assert domain_calls, filename
    assert min(domain_calls) > parse_at, filename
    assert "status_code=400" in source


def test_path_ids_use_the_snowflake_param():
    source = (ROUTES / "deletion.py").read_text(encoding="utf-8")
    assert "SnowflakeIDParam" in source
    assert "user_id: SnowflakeIDParam" in source
