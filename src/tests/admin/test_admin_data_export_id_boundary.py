"""Admin data-export approve/deny must bind the path id through ``parse_id``.

``/data-export/{request_id}/approve`` and ``/.../deny`` used a bare ``int()``
on the caller-controlled path id, while their sibling handlers in the same
module bind the very same segment with ``parse_id`` (GET) or
``validate_export_id`` (GENERATE, itself a parse_id gate). Python's ``int``
accepts Unicode decimal homoglyphs (``"٤٢"`` -> 42), PEP 515 separators
(``"1_2"`` -> 12), signs, ``bool``/``float`` truncation and values past the
64-bit snowflake ceiling. The id that reached the ``fetch_one(...)`` /
``UPDATE`` in the destructive handlers could therefore differ from the one
every other step of the approval workflow read from the same bytes -- a
numeric/id-boundary parser differential on an admin mutation.

These tests pin both handlers to the canonical ASCII snowflake gate and prove
the previously-accepted tokens are now rejected before any DSAR mutation is
attempted. The static AST ratchet stops a bare ``int()`` from creeping back.
"""

import ast
import textwrap
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.api.routes.admin import data_export as routes

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROUTES = (
    Path(__file__).resolve().parents[2] / "api" / "routes" / "admin" / "data_export.py"
)
HANDLERS = ("approve_data_export", "deny_data_export")

# Accepted by bare int() (or raising an uncaught bare ValueError) but rejected
# by the bounded ASCII snowflake gate. Booleans/floats are included because a
# directly-constructed call can bypass Pydantic's SnowflakeIDParam coercion.
BAD_IDS = [
    "١٢٣",  # Arabic-Indic digits -> int() gave 123
    "１２３",  # fullwidth digits -> int() gave 123
    "1_2",  # PEP 515 separator -> int() gave 12
    "-1",  # negative
    "0",  # not a valid resource id
    str(2**63),  # past the snowflake ceiling
    "9" * 5000,  # int()'s digit cap raises a bare ValueError
    "12abc",  # trailing garbage
    True,  # bool truncated to 1
    1.5,  # float truncated to 1
]


# ---------------------------------------------------------------------------
# static AST ratchet
# ---------------------------------------------------------------------------


def _func_node(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"function {name!r} not found")


def _calls_with_arg(node, func_name, arg_name):
    """lineno for every ``func_name(<arg_name>, ...)`` call inside ``node``."""
    hits = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (isinstance(func, ast.Name) and func.id == func_name):
            continue
        if any(isinstance(a, ast.Name) and a.id == arg_name for a in call.args):
            hits.append(call.lineno)
    return sorted(hits)


def test_detector_catches_bare_int_but_accepts_parse_id():
    bad = textwrap.dedent(
        """
        async def approve_data_export(request, request_id):
            await approve_request(int(request_id))
        """
    )
    good = textwrap.dedent(
        """
        async def approve_data_export(request, request_id):
            req_id = parse_id(request_id, "request_id")
            await approve_request(req_id)
        """
    )
    assert _calls_with_arg(ast.parse(bad), "int", "request_id")
    assert not _calls_with_arg(ast.parse(bad), "parse_id", "request_id")
    assert not _calls_with_arg(ast.parse(good), "int", "request_id")
    assert _calls_with_arg(ast.parse(good), "parse_id", "request_id")


def test_handlers_bind_request_id_through_parse_id_not_bare_int():
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    for name in HANDLERS:
        fn = _func_node(tree, name)
        assert not _calls_with_arg(fn, "int", "request_id"), (
            f"{name}: bare int(request_id) crept back into the id boundary"
        )
        assert _calls_with_arg(fn, "parse_id", "request_id"), (
            f"{name}: request_id no longer bound through parse_id"
        )


def test_scan_is_not_vacuous():
    """The real module must actually import and call the shared gate."""
    source = ROUTES.read_text(encoding="utf-8")
    assert "from src.core.security.hardening import parse_id" in source
    tree = ast.parse(source)
    for name in HANDLERS:
        assert _calls_with_arg(_func_node(tree, name), "parse_id", "request_id")


# ---------------------------------------------------------------------------
# runtime: reject before any DSAR mutation
# ---------------------------------------------------------------------------


class _MutationProbe:
    """Records the ids that reach a DSAR mutation stub."""

    def __init__(self):
        self.approve_calls = []
        self.deny_calls = []

    def approve(self, request_id, admin_id):
        self.approve_calls.append(request_id)
        return {}

    def deny(self, request_id, admin_id, reason):
        self.deny_calls.append(request_id)
        return {}


@pytest.fixture
def probe(monkeypatch):
    p = _MutationProbe()
    monkeypatch.setattr(routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        routes, "require_admin_permission", lambda request, permission: 7
    )
    monkeypatch.setattr(routes, "approve_request", p.approve)
    monkeypatch.setattr(routes, "deny_request", p.deny)
    return p


def _deny_body():
    return routes.DenyBody(reason="policy")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
async def test_approve_rejects_noncanonical_id_before_mutation(probe, bad):
    with pytest.raises(HTTPException) as exc:
        await routes.approve_data_export(object(), bad)
    assert exc.value.status_code == 400
    assert probe.approve_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
async def test_deny_rejects_noncanonical_id_before_mutation(probe, bad):
    with pytest.raises(HTTPException) as exc:
        await routes.deny_data_export(object(), bad, _deny_body())
    assert exc.value.status_code == 400
    assert probe.deny_calls == []


@pytest.mark.asyncio
async def test_approve_passes_canonical_id_through(probe):
    await routes.approve_data_export(object(), "123")
    assert probe.approve_calls == [123]


@pytest.mark.asyncio
async def test_deny_passes_canonical_id_through(probe):
    await routes.deny_data_export(object(), "123", _deny_body())
    assert probe.deny_calls == [123]
