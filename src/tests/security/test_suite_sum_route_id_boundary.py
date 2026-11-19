"""SEC400 ratchet: PlexiSum route IDs use the hardened boundary parser.

``int(workbook_id)`` on a string path parameter is not equivalent to the
shared ``parse_id`` gate. The latter is ASCII-only and range-bounded to a
positive snowflake; the former happily turns ``"٤٢"``/``"４２"`` into 42,
``"1_0"`` into 10, and accepts signs and 128-bit values. That parser
differential is the same class the messaging/channel/polls routes were
hardened against (see ``test_sec400_route_id_parse_boundary_invariant.py``),
but the PlexiSum route package was left using bare ``int()`` on every
workbook/sheet/grant/feature id.

This suite ratchets the PlexiSum adopters with an AST check (no app import,
config or DB needed, so it cannot flake) and exercises two handlers directly
to prove malformed ids are rejected *before* the manager is reached.
"""

import ast
import asyncio
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Every PlexiSum route module that converted a string ID with bare int().
# ``operations.py`` is owned by a separate hardening effort and is excluded.
HARDENED_SUM_MODULES = (
    "src/api/routes/suite/sum/comments.py",
    "src/api/routes/suite/sum/features.py",
    "src/api/routes/suite/sum/files.py",
    "src/api/routes/suite/sum/grants.py",
    "src/api/routes/suite/sum/presence.py",
    "src/api/routes/suite/sum/sheets.py",
    "src/api/routes/suite/sum/snapshots.py",
    "src/api/routes/suite/sum/validations.py",
    "src/api/routes/suite/sum/workbooks.py",
    "src/api/routes/suite/sum/xlsx.py",
)

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _is_route_handler(node) -> bool:
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in HTTP_METHODS
        for dec in node.decorator_list
    )


def _id_params(handler):
    """String-typed route parameters whose name looks like an identifier."""
    names = set()
    args = handler.args
    for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        ann = arg.annotation
        is_str = (isinstance(ann, ast.Name) and ann.id == "str") or (
            isinstance(ann, ast.Subscript)
            and isinstance(ann.slice, ast.Name)
            and ann.slice.id == "str"
        )
        if is_str and (arg.arg.endswith("_id") or arg.arg == "id"):
            names.add(arg.arg)
    return names


def _bare_int_violations(tree):
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route_handler(node):
            continue
        params = _id_params(node)
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "int"
                and sub.args
                and isinstance(sub.args[0], ast.Name)
                and sub.args[0].id in params
            ):
                found.append((node.name, sub.args[0].id, sub.lineno))
    return found


def _parse_id_calls(tree) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "parse_id"
    )


def _route_count(tree) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _is_route_handler(node)
    )


def _scan():
    scanned = []
    for rel in HARDENED_SUM_MODULES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        scanned.append(
            (rel, _bare_int_violations(tree), _parse_id_calls(tree), _route_count(tree))
        )
    return scanned


_SCAN = _scan()


def test_detector_catches_bare_int_and_accepts_parse_id():
    bad = """
@router.get("/bad")
def bad(workbook_id: str, sheet_id: str):
    return int(workbook_id), int(sheet_id)
"""
    good = """
@router.get("/good")
def good(workbook_id: str):
    return parse_id(workbook_id, "workbook_id")
"""
    assert len(_bare_int_violations(ast.parse(bad))) == 2
    tree = ast.parse(good)
    assert _bare_int_violations(tree) == []
    assert _parse_id_calls(tree) == 1


def test_scan_is_not_vacuous():
    assert len(_SCAN) == len(HARDENED_SUM_MODULES)
    assert sum(calls for _, _, calls, _ in _SCAN) >= 30
    assert sum(handlers for _, _, _, handlers in _SCAN) >= 40


def test_no_sum_route_converts_a_boundary_id_with_bare_int():
    offenders = [
        f"{rel}:{line} in {handler}({param})"
        for rel, violations, _, _ in _SCAN
        for handler, param, line in violations
    ]
    assert not offenders, (
        "Bare int() on a PlexiSum string ID route parameter reintroduced:\n  "
        + "\n  ".join(sorted(offenders))
    )


@pytest.mark.parametrize(
    "malformed",
    [
        "٤٢",  # Arabic-Indic digits: int() -> 42
        "４２",  # fullwidth digits: int() -> 42
        "1_0",  # PEP 515 separator: int() -> 10
        "-1",
        "0",
        "99999999999999999999",  # past the snowflake ceiling
    ],
)
@pytest.mark.parametrize("handler_name", ["get_workbook", "download_file"])
def test_malformed_workbook_id_is_rejected_before_manager(
    monkeypatch, malformed, handler_name
):
    from src.api.routes.suite import sum as pkg

    called = []

    class ForbiddenManager:
        def get_workbook(self, *_args, **_kwargs):
            called.append("get_workbook")
            raise AssertionError("manager must not be reached")

        def download_file(self, *_args, **_kwargs):
            called.append("download_file")
            raise AssertionError("manager must not be reached")

    monkeypatch.setattr(pkg, "_manager", lambda: ForbiddenManager())
    handler = getattr(pkg, handler_name)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            handler(
                workbook_id=malformed,
                current_user=types.SimpleNamespace(user_id=42),
            )
        )
    assert exc.value.status_code == 400
    assert called == []


def test_valid_workbook_id_reaches_manager(monkeypatch):
    """Non-vacuity: an ASCII snowflake still flows through to the manager."""
    from src.api.routes.suite import sum as pkg

    seen = []

    class CapturingManager:
        def get_workbook(self, user_id, workbook_id):
            seen.append((user_id, workbook_id))
            raise RuntimeError("stop here")

    monkeypatch.setattr(pkg, "_manager", lambda: CapturingManager())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            pkg.get_workbook(
                workbook_id="42",
                current_user=types.SimpleNamespace(user_id=7),
            )
        )
    assert seen == [(7, 42)]
    assert exc.value.status_code == 500
