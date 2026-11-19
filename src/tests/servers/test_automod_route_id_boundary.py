"""Automod route ids must pass the shared ASCII-only snowflake gate.

The automod handlers parsed their URL/query ids with bare ``int()``. Python's
``int`` accepts Unicode decimal homoglyphs (``"１２"`` -> 12), PEP 515
underscores (``"1_2"`` -> 12), a leading sign/zero and arbitrarily large
integers, so the id that reached ``require_permission``, the rule/violation
lookup and storage could differ from the one the caller actually wrote -- and
disagreed with every other route boundary, which parses ids with ``parse_id``.
All ids now go through that canonical gate and reject non-canonical ids with
400 before any permission check or service call.
"""

import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes.servers import automod as routes
from src.core.security.hardening import SNOWFLAKE_MAX

# Every one of these is accepted by int() (or raises a bare ValueError deep in
# a handler) but rejected by the bounded ASCII-only gate. They previously
# flowed into permission checks / lookups as a different identifier than the
# caller wrote.
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
def test_parse_id_rejects_noncanonical_id(bad_id):
    with pytest.raises(HTTPException) as exc:
        routes._parse_id(bad_id, "server ID")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == 400
    assert exc.value.detail["error"]["message"] == "Invalid server ID"


@pytest.mark.parametrize("raw", ["1", "42", str(SNOWFLAKE_MAX), "  123  ", "+7"])
def test_canonical_ids_are_parsed_unchanged(raw):
    assert routes._parse_id(raw, "server ID") == int(raw.strip())


def test_get_rules_rejects_homoglyph_id_before_permission_or_service(monkeypatch):
    """A handler must not touch authz/service layers for a non-canonical id."""
    calls = []

    class _Boom:
        def get_servers(self):
            return self

        def require_permission(self, *args, **kwargs):
            calls.append(("require_permission", args, kwargs))
            raise AssertionError("authorization reached with a bad id")

        def __getattr__(self, name):
            def fail(*args, **kwargs):
                calls.append((name, args, kwargs))
                raise AssertionError("service layer reached with a bad id")

            return fail

    monkeypatch.setattr(routes.api, "get_servers", lambda: _Boom())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_server_automod_rules("１２", current_user=object()))
    assert exc.value.status_code == 400
    assert calls == []


def test_update_rule_rejects_homoglyph_ids_before_any_service_call(monkeypatch):
    """Both path ids are gated before permission checks or rule lookups."""
    calls = []

    class _Boom:
        def get_servers(self):
            return self

        def require_permission(self, *args, **kwargs):
            calls.append(("require_permission", args, kwargs))
            raise AssertionError("authorization reached with a bad id")

        def __getattr__(self, name):
            def fail(*args, **kwargs):
                calls.append((name, args, kwargs))
                raise AssertionError("service layer reached with a bad id")

            return fail

    monkeypatch.setattr(routes.api, "get_servers", lambda: _Boom())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes.update_server_automod_rule(
                "١٢", "٤٢", body=object(), current_user=object()
            )
        )
    assert exc.value.status_code == 400
    assert calls == []
