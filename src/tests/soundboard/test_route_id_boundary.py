"""Soundboard route ids must pass the shared ASCII-only snowflake gate.

The soundboard handlers parsed their URL ids with bare
``int(str(value).strip())``. Python's ``int`` accepts Unicode decimal
homoglyphs (``"１２"`` -> 12), PEP 515 underscores (``"1_2"`` -> 12), a leading
sign and arbitrarily large integers, so the id that reached the permission
check, cache key and storage could differ from the one the caller actually
wrote -- and disagreed with every other route boundary, which parses ids with
``parse_id``. The two route helpers now use that canonical gate and reject
non-canonical ids with 400 before any service call.
"""

import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import soundboard as routes
from src.core.security.hardening import SNOWFLAKE_MAX

# Every one of these is accepted by int() but rejected by the bounded
# ASCII-only gate. They previously flowed into permission/cache/storage as a
# different identifier than the caller wrote.
_BAD_IDS = [
    "-1",  # negative
    "0",  # zero is not a valid snowflake here
    str(SNOWFLAKE_MAX + 1),  # just past the signed 64-bit ceiling
    str(2**80),  # far past it (must not 500 on conversion)
    "9" * 5000,  # oversized token must fail validation, not int()'s digit cap
    "1_2",  # PEP 515 underscore -> int 12
    "٤٢",  # Arabic-Indic digits -> int 42
    "１２",  # fullwidth digits -> int 12
    "1.5",  # fraction int() would reject with a bare ValueError
    "0x10",  # non-decimal prefix int() rejects with a bare ValueError
]


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_parse_server_id_rejects_noncanonical_id(bad_id):
    with pytest.raises(HTTPException) as exc:
        routes._parse_server_id(bad_id)
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == 400


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_parse_int_rejects_noncanonical_id(bad_id):
    with pytest.raises(HTTPException) as exc:
        routes._parse_int(bad_id, "sound ID")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["message"] == "Invalid sound ID"


@pytest.mark.parametrize("raw", ["1", "42", str(SNOWFLAKE_MAX), "  123  ", "+7"])
def test_canonical_ids_are_parsed_unchanged(raw):
    assert routes._parse_server_id(raw) == int(raw.strip())
    assert routes._parse_int(raw, "sound ID") == int(raw.strip())


def test_route_rejects_homoglyph_id_before_service_call(monkeypatch):
    """A handler must not touch the soundboard layer for a non-canonical id."""
    calls = []

    class _Boom:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                calls.append((name, args, kwargs))
                raise AssertionError("service layer reached with a bad id")

            return fail

    monkeypatch.setattr(routes, "_get_soundboard", lambda: _Boom())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_sound("１２", "٤٢", current_user=object()))
    assert exc.value.status_code == 400
    assert calls == []
